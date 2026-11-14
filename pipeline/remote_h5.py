"""Read a remote HDF5 (ASF / Earthdata) with HTTP Range requests, so only the bytes that are needed travel.

h5py accepts any Python file-like object. NISAR GCOV datasets are chunked, so reading HH/HV inside a window
fetches only those chunks (plus small metadata blocks) instead of the whole 2–13 GB file.

    with RemoteFile(url, session) as f, h5py.File(f, "r") as h5: ...

The ASF URL redirects to a short-lived signed URL; it is resolved once and refreshed when it expires.
"""
import io
import threading
from collections import OrderedDict

import requests

BLOCK = 4 << 20  # 4 MB blocks: big enough to amortise latency, small enough not to waste bytes


class RemoteFile(io.RawIOBase):
    def __init__(self, url: str, session: requests.Session, block: int = BLOCK, cache_blocks: int = 64):
        self.url, self.s, self.block, self.cache_blocks = url, session, block, cache_blocks
        self.pos, self.fetched = 0, 0
        self._pre: dict[int, bytes] = {}  # chunk byte offset -> raw (compressed) chunk, from prefetch()
        self._cache: OrderedDict[int, bytes] = OrderedDict()
        self._lock = threading.Lock()
        self._resolve()

    def _resolve(self):
        r = self.s.get(self.url, headers={"Range": "bytes=0-0"}, allow_redirects=True, timeout=60)
        r.raise_for_status()
        if r.status_code != 206:
            raise OSError("servidor não aceita leitura parcial (Range)")
        self.signed = r.url  # signed S3/CloudFront URL: no auth header needed (and must not be sent to it)
        self.size = int(r.headers["Content-Range"].split("/")[-1])

    def _get(self, start: int, end: int) -> bytes:
        for attempt in range(4):
            try:
                r = requests.get(self.signed, headers={"Range": f"bytes={start}-{end}"}, timeout=120)
                if r.status_code in (401, 403):  # signed URL expired
                    self._resolve()
                    continue
                r.raise_for_status()
                self.fetched += len(r.content)
                return r.content
            except requests.RequestException:
                if attempt == 3:
                    raise
        raise OSError("falha ao ler intervalo remoto")

    def _block(self, i: int) -> bytes:
        with self._lock:
            if i in self._cache:
                self._cache.move_to_end(i)
                return self._cache[i]
        start = i * self.block
        data = self._get(start, min(self.size, start + self.block) - 1)
        with self._lock:
            self._cache[i] = data
            while len(self._cache) > self.cache_blocks:
                self._cache.popitem(last=False)
        return data

    def prefetch(self, dset, rows: slice, cols: slice, workers: int = 8, merge_gap: int = 1 << 16) -> None:
        """Fetch, in parallel, every stored chunk of a 2-D dataset that intersects [rows, cols].

        HDF5 would otherwise request them one by one (latency-bound). Neighbouring chunks are merged into
        one request when they sit close together in the file.
        """
        from concurrent.futures import ThreadPoolExecutor
        cr, cc = dset.chunks
        want = []
        for r0 in range(rows.start // cr * cr, rows.stop, cr):
            for c0 in range(cols.start // cc * cc, cols.stop, cc):
                info = dset.id.get_chunk_info_by_coord((r0, c0))
                if info.byte_offset is not None and info.size:
                    want.append((info.byte_offset, info.size))
        want.sort()
        runs = []  # [start, end, [(offset, size), ...]]
        for off, size in want:
            if runs and off - runs[-1][1] <= merge_gap and runs[-1][1] - runs[-1][0] < 32 << 20:
                runs[-1][1] = max(runs[-1][1], off + size)
                runs[-1][2].append((off, size))
            else:
                runs.append([off, off + size, [(off, size)]])

        def fetch(run):
            start, end, parts = run
            data = self._get(start, end - 1)
            return {o: data[o - start:o - start + s] for o, s in parts}

        with ThreadPoolExecutor(workers) as ex:
            for d in ex.map(fetch, runs):
                self._pre.update(d)

    def drop_prefetch(self):
        self._pre.clear()

    # --- file-like API used by h5py
    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos

    def seek(self, off, whence=io.SEEK_SET):
        self.pos = off if whence == io.SEEK_SET else self.pos + off if whence == io.SEEK_CUR else self.size + off
        return self.pos

    def readinto(self, b):
        n = min(len(b), max(0, self.size - self.pos))
        if n <= 0:
            return 0
        pre = self._pre.get(self.pos)
        if pre is not None and n <= len(pre):  # a prefetched chunk (HDF5 reads each chunk in one call)
            b[:n] = pre[:n]
            self.pos += n
            return n
        if n > 2 * self.block:  # big contiguous read (a whole chunk run): one request, bypass the cache
            b[:n] = self._get(self.pos, self.pos + n - 1)
            self.pos += n
            return n
        out, p, end = memoryview(b), self.pos, self.pos + n
        while p < end:
            i = p // self.block
            data = self._block(i)
            o = p - i * self.block
            k = min(end - p, len(data) - o)
            out[p - self.pos:p - self.pos + k] = data[o:o + k]
            p += k
        self.pos = end
        return n

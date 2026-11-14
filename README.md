# Dancing with the SARs — Amazônia, El Niño/La Niña e as pessoas do rio

Projeto para o desafio **Dancing with the SARs** do NASA Space Apps Challenge 2026.

O app junta três camadas de dados:

1. **Radar NISAR (banda L):** mostra água aberta e **floresta alagada sob a copa**. Isso só a banda L consegue enxergar, e o radar funciona com qualquer cobertura de nuvens.
2. **El Niño / La Niña (ONI, NOAA):** o motor climático da cheia e da seca na Amazônia. Em ASO/2026 o ONI está em **+2,46 °C, um El Niño muito forte**.
3. **População (WorldPop) em 3D:** mostra quem vive perto da água que avança ou recua.

Um modelo de ML usa o ONI e o histórico para prever a chuva ou o nível do rio com 1, 3 e 6 meses de antecedência. A avaliação é honesta: o modelo é comparado com a climatologia e com a persistência.

## Estrutura

```
config/aois.json            áreas de estudo (Manaus, Tefé/Mamirauá) e trilha NISAR preferida
pipeline/
  fetch_oni.py              ONI da NOAA CPC (1950 → hoje)
  fetch_power.py            chuva mensal da NASA POWER (1981 → hoje, sem login)
  fetch_ana.py              cota do rio da ANA: CSV do HidroWeb (histórico) + telemetria (recente)
  fetch_population.py       WorldPop 2020 ~1 km → grade do radar + grade estendida (±1°) para o fogo
  fetch_landcover.py        ESA WorldCover 10 m → fração urbana/água/área úmida (lido direto da AWS)
  fetch_fires.py            focos FIRMS/MODIS 2000 → hoje (ano atual via API, precisa de FIRMS_MAP_KEY)
  search_nisar.py           catálogo NISAR GCOV/GUNW via ASF; --plan (datas + trilhas), --validate
  remote_h5.py              lê HDF5 remoto por HTTP Range (só os blocos necessários)
  process_gcov.py           GCOV HDF5 → mosaico 100 m, dB, classes (água/floresta alagada/urbano), WebP e exposição
  validate_optical.py       valida a água do radar com Sentinel-2 (MNDWI) e marca referências com névoa/fumaça
  detect_burn_scars.py      cicatrizes de queimada (queda de HV) cruzadas com focos e com controle aleatório
  nowcast_fire.py           "Fogo agora": focos de 48 h + previsão do tempo de 72 h → risco, vento e fumaça
  fetch_energy.py           hidrelétricas (ONS: vazão, nível, geração, capacidade) + sol e aerossóis (NASA POWER)
  render_solar.py           mapa solar suave em relevo (curvas de nível) na mesma área do fogo
  run_all.py                roda tudo que não precisa de login
model/
  train_forecast.py         previsão de rio, chuva e fogo; métricas, referências, P10–P90 e importância
  fire_modulation.py        quanto El Niño e rio baixo elevam o fogo acima do patamar recente
  energy_analysis.py        água → energia nas hidrelétricas, sol × fumaça e risco de seca severa para o diesel
web/                        app Vite + deck.gl + MapLibre + Chart.js, em 4 modos: Rios · Fogo · Fogo agora · Energia
  public/data/              JSONs e imagens (WebP) gerados pelo pipeline (servidos em data/)
  Dockerfile, nginx.conf    imagem do site (build Node → nginx)
docker-compose.yml          publica o site; com --profile https, o Caddy cuida do HTTPS do domínio
deploy/Caddyfile            proxy HTTPS (domínio vem da variável DOMAIN)
```

### Os quatro modos do app

| Modo | Mapa | Gráficos |
|---|---|---|
| 💧 **Rios** | radar NISAR (água, floresta alagada, urbano), óptico Sentinel-2, população junto à água | ONI × Rio Negro; previsão do rio ou da chuva |
| 🔥 **Fogo** | focos dos 60 dias antes de cada data do radar, cicatrizes (ΔHV), população perto do fogo (±1°) | focos/ano no Amazonas; previsão de focos |
| 📡 **Fogo agora** | cartões dos maiores aglomerados (clique para ir até o foco), focos das últimas 48 h agrupados por risco, setas de vento (+0 a +69 h), setores de fumaça, população na rota da fumaça | 72 h de índice de Angström, vento e chuva |
| ⚡ **Energia** | cartões das 5 usinas (água × normal e geração; clique para ver os gráficos); mesma área do Fogo (±1°): potencial solar em relevo (NASA POWER; a grade de 1° é só suavizada visualmente) e as hidrelétricas (altura = potência, cor = vazão atual × normal); a lista e os gráficos cobrem as 5 usinas | água × geração da usina; geração na seca ano a ano; sol × fumaça |

**Fogo agora** é um retrato do dia, para rodar de novo sempre que quiser atualizar. Ele não é um modelo validado nem um alerta oficial:
- **Focos:** FIRMS NRT, com VIIRS (375 m, detecta mais focos pequenos) e MODIS.
- **Tempo:** previsão horária do [Open-Meteo](https://open-meteo.com/) (ECMWF/GFS, sem chave).
- **Risco:** índice de Angström (RH/20 + (27 − T)/10) no horário mais seco das próximas 24 h, mais a chuva e as rajadas previstas.
- **Fumaça:** setor de 40 km a favor do vento médio das próximas 12 h, ou um círculo de 10 km se o vento estiver calmo.

```bash
.venv/Scripts/python pipeline/nowcast_fire.py --aoi manaus
```

## Como rodar

### 1. Python (dados e modelo)

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python pipeline/run_all.py
```

### 2. Radar NISAR (precisa de uma conta gratuita no NASA Earthdata)

Gere um token em https://urs.earthdata.nasa.gov (menu *Generate Token*). Coloque-o na variável de ambiente `EARTHDATA_TOKEN`, **sem colar no código**. Depois rode:

```bash
.venv/Scripts/python pipeline/search_nisar.py --plan                      # escolhe datas e trilhas (não baixa nada)
.venv/Scripts/python pipeline/process_gcov.py --aoi manaus --workers 4    # lê só o necessário, direto da ASF
.venv/Scripts/python pipeline/validate_optical.py --aoi manaus
.venv/Scripts/python pipeline/detect_burn_scars.py --aoi manaus
```

**Área e datas.** O radar cobre a mesma área das abas de fogo e energia (área de estudo ± 1°), em **todas as passagens da trilha principal** (Manaus: 10 datas, Tefé: 9, de junho a novembro de 2026). Uma passagem sozinha cobre de 55% a 80% dessa área. O `--plan` completa as bordas com a passagem de **outra trilha, a até 12 dias** da principal, e a cobertura sobe para 90–97%. Só entram passagens com HH; as que têm só VV ficam de fora. Cada trilha recebe o seu próprio limiar de água, e a banda 3 do GeoTIFF guarda de qual passagem veio cada pixel.

**Leitura remota, sem baixar os arquivos.** Cada GCOV tem de 2 a 13 GB, mas o `process_gcov.py` abre o HDF5 pela internet (`pipeline/remote_h5.py`, com requisições HTTP *Range* em paralelo) e lê só os blocos de HH e HV dentro da área. Ele usa a **frequencyB (80 m)**, que tem cerca de 1/20 dos bytes da banda de 20 m, e que vamos reduzir para 100 m de qualquer jeito. Num teste em Tefé, a água classificada a partir da frequencyB concordou com a da frequencyA em **F1 0,97**. Quando a cena não tem frequencyB, ele lê a A e faz a média 4×4. Resultado: **150 a 220 MB e cerca de 2 minutos por data**, contra 5 a 13 GB de download, e nada bruto fica no disco.

**Dataset local.** Cada data vira `~/spaceapps_data/nisar/<aoi>/<data>_100m.tif`, com cerca de 17 MB, fora do OneDrive (int16 em dB×10: banda 1 = HH, 2 = HV, 3 = passagem). O `manifest.json` ao lado registra os granules usados, o MD5 do catálogo, a frequência lida, os MB lidos e o sha256 do GeoTIFF. Daí em diante, o pipeline e o app funcionam sem acessar a ASF. Para **só validar** o dataset contra o catálogo, sem baixar nada (o comando confere o MD5 de cada granule e lista passagens novas):

```bash
.venv/Scripts/python pipeline/search_nisar.py --validate
.venv/Scripts/python pipeline/process_gcov.py --aoi manaus --reingest   # só se a validação acusar reprocessamento
```

O limiar de água é calculado por passagem, no vale do histograma de HH em dB (com o Otsu como reserva). A floresta alagada usa `FLOODED_FOREST_DB` (−3 dB); pixels claros em área urbana do WorldCover viram a classe "urbano", porque prédios também fazem duplo salto. A validação usa o MNDWI (verde − SWIR), e não o NDWI, porque o Solimões barrento reflete no infravermelho próximo. Os dados brutos ficam em `~/spaceapps_data/raw`, fora do OneDrive.

### 3. Nível do rio (opcional; o serviço da ANA cai com frequência)

Baixe o zip da estação **14990000** no [HidroWeb](https://www.snirh.gov.br/hidroweb/serieshistoricas) (tem reCAPTCHA, então o download é manual) e coloque em `data/ana/`. O script completa os anos recentes com a telemetria da ANA.

```bash
.venv/Scripts/python pipeline/fetch_ana.py --aoi manaus --csv data/ana/<zip da estação>
.venv/Scripts/python model/train_forecast.py --aoi manaus --target river --split 2010-12-01
```

A estação de Tefé (12900000) só tem dados até 1994 e não tem telemetria, por isso não é usada.

### 4. App web

```bash
cd web
npm install
npm run dev
```

### 5. Publicar num domínio (Docker)

O site é estático: os dados que o pipeline gera ficam em `web/public/data` e entram na imagem. O `docker-compose.yml` compila o app (Node) e o serve com nginx. Não precisa de banco, de chaves nem do Python no servidor.

```bash
docker compose up -d --build          # http://<servidor>:8080  (porta: PORT no .env)
```

Para usar um domínio com **HTTPS automático**, aponte o DNS do domínio (registro A ou AAAA) para o servidor, deixe as portas 80 e 443 abertas e rode:

```bash
cp .env.example .env                  # edite DOMAIN=seu.dominio
docker compose --profile https up -d --build
```

O Caddy emite e renova o certificado sozinho, e guarda os certificados num volume persistente.

**Para atualizar os dados** (por exemplo, depois de rodar o "Fogo agora" ou uma nova passagem do NISAR), rode o pipeline na sua máquina e depois `docker compose up -d --build`. Outra opção é descomentar o volume `./web/public/data` no `docker-compose.yml`: assim o servidor passa a usar os arquivos novos sem rebuild.

O build também funciona sem Docker, em qualquer hospedagem estática (Netlify, Cloudflare Pages, GitHub Pages…), porque os caminhos são relativos e funcionam até numa subpasta:

```bash
cd web && npm run build               # publique a pasta web/dist
```

Os dados no ar são um retrato da última vez que o pipeline rodou. O "Fogo agora", em especial, só se atualiza quando é rodado de novo.

## Fontes de dados

| Dado | Fonte |
|---|---|
| NISAR L2 GCOV / GUNW | NASA-ISRO via [ASF](https://search.asf.alaska.edu/) · [guia](https://nisar-docs.asf.alaska.edu/) |
| ONI | [NOAA CPC](https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt) |
| Precipitação | [NASA POWER](https://power.larc.nasa.gov/) (PRECTOTCORR) |
| Cota do rio | [ANA HidroWeb](https://www.snirh.gov.br/hidroweb/) |
| População | [WorldPop](https://www.worldpop.org/) 2020, 1 km, UN-adjusted |
| Cobertura do solo | [ESA WorldCover](https://esa-worldcover.org/) 2021, 10 m |
| Óptico (validação) | Sentinel-2 L2A via [Earth Search](https://earth-search.aws.element84.com/v1) |
| Focos de fogo | [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/) (MODIS para a série; VIIRS + MODIS NRT para "agora") |
| Contorno do Amazonas | [IBGE malhas](https://servicodados.ibge.gov.br/api/docs/malhas) |
| Previsão do tempo | [Open-Meteo](https://open-meteo.com/) (ECMWF/GFS, horária, 72 h) |
| Hidrelétricas | [ONS dados abertos](https://dados.ons.org.br/) (vazão, nível e geração por usina) |
| Sol e aerossóis | [NASA POWER](https://power.larc.nasa.gov/) (GHI e AOD 550 nm, MERRA-2/CERES) |

## Resultados (rios)

- **Radar (19 datas, área de estudo ± 1°):** de jun a nov/2026, na região de Manaus, a água aberta caiu 27% (7.259 → 5.327 km²) e a floresta alagada caiu 98% (6.041 → 113 km²). Em Tefé, de ago a out (mesma cobertura), a floresta alagada caiu 98% (3.828 → 77 km²).
- **Validação com Sentinel-2 (área toda):** F1 0,89 em Manaus (8 de 10 datas confiáveis) e 0,81 em Tefé (8 de 9). Na área original, F1 0,91 e 0,94. Uma data só entra na conta se o óptico enxergar pelo menos 70% do rio permanente (WorldCover). Em 21/10 e 02/11, névoa ou fumaça cobriam Manaus, e em 23/10 cobriam Tefé.
- **Ressalva de Tefé (jun–jul):** nessas datas, a cena 175 da trilha principal só veio com a frequencyA em modo `DHNA`, e o conteúdo é preenchimento (~−54 dB). Esses pixels são descartados (abaixo de −40 dB), e a cobertura cai para 56–66%.
- **Modelo do rio:** com 3 meses de antecedência, skill +0,25 sobre a climatologia, superando também a persistência. Previsão para nov/2026: 17,2 m (normal: 18,7 m).

## Resultados (incêndios)

- **Dados:** focos MODIS (NASA FIRMS) de 2000 a nov/2026 no estado do Amazonas (contorno do IBGE), só fogo em vegetação e confiança ≥ 30. Até ago/2026 vem do produto padrão; depois, do NRT (via API, com `FIRMS_MAP_KEY`). Usamos só o MODIS para a série ser consistente (o VIIRS começa em 2012), e os satélites Terra e Aqua mantêm a mesma proporção em 2026.
- **Patamar humano:** a temporada ago–out passou de cerca de 10 mil focos (2000–2015) para 20 a 34 mil (2021–2024; o recorde foi 2024). Por isso, "igual aos 3 anos anteriores" é a referência mais difícil de bater: skill +0,25, contra +0,16 do modelo ML.
- **Modulação climática** em relação ao patamar recente (24 temporadas): **rio baixo em Manaus (mínima < 16,3 m) +39%**, demais anos −10%. Já o efeito do El Niño é praticamente nulo (+1%): 2026 tem o El Niño mais forte da série (ONI 2,14), mas o rio desceu só até 16,45 m e o fogo no estado ficou em 0,55 do patamar recente. **O que conta é a seca que chega ao rio, não o índice do Pacífico sozinho.**
- **2026 em volta de Manaus:** 3,5 mil focos, o 2º maior ano da série (atrás só de 2023).
- **Radar × fogo:** na área ampliada de Manaus, a queda de HV na floresta (23/06 → 02/11/2026, só pixels vistos pela trilha principal nas duas datas) aponta **222 manchas (34 km²)**. **204 delas (92%) têm foco FIRMS a até cerca de 1,7 km, contra 12% de pontos de floresta sorteados ao acaso.** O NISAR está enxergando cicatrizes de queimada. As manchas sem foco podem ser desmatamento ou fogo de sub-bosque. Em Tefé (66 focos no período) não apareceu nenhuma mancha.

```bash
.venv/Scripts/python pipeline/fetch_fires.py
.venv/Scripts/python model/fire_modulation.py
.venv/Scripts/python model/train_forecast.py --aoi amazonas --target fire --split 2015-12-01
.venv/Scripts/python pipeline/detect_burn_scars.py --aoi manaus
```

## Resultados (energia)

- **Água → energia:** nas usinas de fio d'água a geração acompanha o rio. Em **Belo Monte**, a correlação vazão × geração é 0,86, e na seca −10% de água ≈ −12% de energia. Na seca de 2024, Belo Monte, Santo Antônio e Jirau geraram **54% do normal** (2023: 66%; 2026: 90%).
- **Clima → água:** a água que chega a **Balbina** (abastece Manaus) cai com o El Niño (Spearman −0,68) e acompanha a mínima do Rio Negro (+0,63). Em nov/2026 ela está em **30% do normal**, mas o reservatório ainda sustenta 62% da capacidade. O Madeira, alimentado pelos Andes, está normal.
- **Sol × fumaça (NASA POWER):** cerca de 4,7 kWh/m²/dia. Nos meses mais enfumaçados de ago–out a irradiação é cerca de 8% menor (correlação −0,58). É uma associação: as nuvens também variam.
- **Diesel pelo rio:** "seca severa" = mínima anual entre as 10% mais baixas (< 15,4 m em Manaus). Pela previsão do rio, a chance em nov/2026 é de 2%.

```bash
.venv/Scripts/python pipeline/fetch_energy.py
.venv/Scripts/python pipeline/render_solar.py
.venv/Scripts/python model/energy_analysis.py
```

## Próximos passos

- [x] NISAR em 19 datas (Manaus 10, Tefé 9) na área ampliada, com leitura remota parcial, máscara urbana e validação óptica
- [x] Publicação com Docker (nginx + HTTPS automático com Caddy)
- [ ] Mais cidades com o mesmo recorte (Parintins, Coari, Tabatinga), cerca de 30 min de processamento cada
- [x] Régua de Manaus 1902 → hoje (HidroWeb + telemetria) e modelo do nível do rio sem vazamento
- [x] Incêndios: focos FIRMS 2000 → nov/2026, modelo de fogo, modulação ENSO/rio e cicatrizes no radar confirmadas por focos
- [ ] Série longa com Sentinel-1 (2015 →) para cobrir os El Niños de 2015 e 2023
- [ ] Interferometria (GUNW) para deformação do solo
- [ ] Modo "história" com o roteiro da apresentação

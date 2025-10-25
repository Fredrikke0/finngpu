# FinnGPU

Henter GPU annonser fra Finn.no.

### Hovedscript
- `finngpu.py` - Henter og filtrerer priser fra Finn.
- `gpu_analysis.py` - Bruker dataene fra FinnGPU for å gjøre analyse. Beste pris for hver modell, pris/fps etc.

#### Resten av filene
- `gpus.json` - Liste over gpuer å lete etter. FinnGPU bruker en ganske enkel regex for modeller, så hvis du legger til kort som ikke følger amd eller nvidia sin "prefix modellnummer suffix" syntax, så må nok syntaxen oppdateres.
- `1440p-ultra-performance-csv.txt` - GPU fps hentet [herifra](https://www.tomshardware.com/reviews/gpu-hierarchy,4388.html)
- `blacklist.txt` - Svarteliste for å filtrere ut annonser scriptet ikke takler.
- `whitelist.txt` - Du kan også lage en whitelist, jeg brukte den bare for debugging.


## Usage

1. Basic GPU listing fetch:
```bash
python finngpu.py
```

2. Analyze GPU price/performance:
```bash
python gpu_analysis.py -p 1440p-ultra-performance-csv.txt
```

3. Process existing data:
```bash
python finngpu.py -f existing_data.csv
```

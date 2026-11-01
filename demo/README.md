# Ukázková data (TEST DATA)

Smyšlený obsah pro první seznámení s aplikací: firma **Alfa Trade s.r.o.**, lidé Petr Novák a Eva Malá,
tři hlasové poznámky, schválený zápis ze schůzky, soukromá směrnice, veřejný postup a jeden „rozhovor“
(video s neexistujícím ID `demoVideo01`; odkazy na YouTube nevedou nikam). Audio je jen tón, přepisy jsou
předem napsané. Všechno má v názvu `[DEMO]`.

```bash
python demo/load.py       # zkopíruje soubory do vaultu, přidá entity, přestaví index
python demo/remove.py     # všechno zase odstraní (soubory, entity, index)
```

Co bylo přidáno, je zapsáno v `99_System/state/demo_loaded.json`. `load.py` nepřepíše existující soubory
(leda s `--force`) a `remove.py` neodstraní entitu, kterou jste mezitím potvrdili.

Data vznikla skutečnou pipeline (`tools/make_demo.py` v původním projektu) s napodobeninou AI modelů,
takže mají stejnou strukturu jako ostrý obsah.

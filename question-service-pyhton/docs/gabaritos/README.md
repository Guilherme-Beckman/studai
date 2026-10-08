# Gabaritos

Coloque aqui os PDFs de gabarito com texto selecionável. A importação CLI exige associar cada arquivo ao JSON classificado da prova correspondente, incluindo a fase/tipo no arquivo de questões:

```bash
python diagnostico.py importar-gabarito \
  --gabarito docs/gabaritos/simulado2027-s1.pdf \
  --questoes simulados/processados/simulado2027-s1/questoes.classificado.json
```

Se a estrutura do PDF não permitir leitura inequívoca, use um JSON UTF-8 com o formato `{"gabarito":{"1":"A","2":"C"}}` e importe-o pelo mesmo comando. Uma substituição de respostas existentes exige `--replace`.

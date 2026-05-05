# Scraper de Bulas ANVISA

##  Descrição

Este projeto realiza a **extração automatizada de bulas de
medicamentos** diretamente do site da ANVISA:
https://consultas.anvisa.gov.br/#/bulario/

Funcionalidades: -  Busca automática de medicamentos -  Download de
bulas (Paciente e Profissional) -  Organização em pastas separadas -
Recarregamento automático em caso de erro -  Suporte a lista de
medicamentos

Scraper ANVISA Bulário Eletrônico - versão robusta

Baixa bulas do PACIENTE e do PROFISSIONAL em:
https://consultas.anvisa.gov.br/#/bulario/



##  Requisitos

``` bash
pip install selenium webdriver-manager pandas
```

## Como executar

## Uso:
    pip install selenium webdriver-manager pandas
    python scraper_bulas_anvisa_corrigido.py --medicamento dipirona
    python scraper_bulas_anvisa_corrigido.py --medicamentos dipirona paracetamol ibuprofeno
    python scraper_bulas_anvisa_corrigido.py --arquivo-medicamentos medicamentos.txt --max-paginas 2 --max-reloads 5

## Exemplo medicamentos.txt
dipirona
paracetamol
ibuprofeno
amoxicilina
dramin

## Observações:
- Não exige ENTER. Se aparecer captcha, resolva manualmente no navegador; o script continua sozinho quando a tabela aparecer.
- Quando a tabela não aparece, o script recarrega a página e tenta novamente.
- Se ainda falhar, salva debug_anvisa.html e debug_anvisa.png na pasta do medicamento.

### Lista direta

``` bash
python scraper_bulas_anvisa_corrigido_v3.py --medicamentos dipirona paracetamol --max-paginas 2 --max-reloads 5
```

### Arquivo .txt

``` bash
python scraper_bulas_anvisa_corrigido_v3.py --arquivo-medicamentos medicamentos.txt --max-paginas 2 --max-reloads 5
```

## Parâmetros

-   --medicamentos
-   --arquivo-medicamentos
-   --max-paginas
-   --max-reloads


## Estrutura

    bulas/
    ├── Bula_Paciente/
    ├── Bula_Profissional/


## Observações

-   Pode haver CAPTCHA
-   Depende da estrutura do site da ANVISA


## Possíveis evoluções

-   Dataset CSV
-   NLP
-   Dashboard BI

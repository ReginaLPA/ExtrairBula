import csv
import os
import re
import sys
import time
import shutil
import argparse
from pathlib import Path
from datetime import datetime
from urllib.parse import quote
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException

BASE_URL = "https://consultas.anvisa.gov.br/#/bulario/"


def normalizar_nome(texto: str, limite: int = 120) -> str:
    texto = (texto or "sem_nome").strip()
    texto = re.sub(r"[\\/:*?\"<>|]+", "-", texto)
    texto = re.sub(r"\s+", "_", texto)
    texto = re.sub(r"_+", "_", texto)
    return texto[:limite].strip("._-") or "sem_nome"


def criar_estrutura_saida(base_dir: str, medicamento: str):
    raiz = Path(base_dir) / normalizar_nome(medicamento)
    tmp = raiz / "_downloads_tmp"
    paciente = raiz / "paciente"
    profissional = raiz / "profissional"
    for pasta in (tmp, paciente, profissional):
        pasta.mkdir(parents=True, exist_ok=True)
    return raiz, tmp, paciente, profissional


def configurar_driver(download_tmp: Path, headless: bool = False):
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    prefs = {
        "download.default_directory": str(download_tmp.resolve()),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True,
        "safebrowsing.enabled": True,
    }
    options.add_experimental_option("prefs", prefs)
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
    return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)


def salvar_debug(driver, raiz: Path, motivo: str = ""):
    html = raiz / "debug_anvisa.html"
    png = raiz / "debug_anvisa.png"
    try:
        html.write_text(driver.page_source, encoding="utf-8")
    except Exception:
        pass
    try:
        driver.save_screenshot(str(png))
    except Exception:
        pass
    print("\n A tabela não foi localizada.")
    if motivo:
        print(f"Motivo provável: {motivo}")
    print(f"HTML salvo em: {html}")
    print(f"Print salvo em: {png}")
    print("Confira se há captcha, mensagem de erro, campo não preenchido ou site fora do ar.")


def arquivos_pdf(pasta: Path):
    return {p for p in pasta.glob("*.pdf") if p.is_file()}


def aguardar_download_pdf(download_tmp: Path, antes: set[Path], timeout: int = 90) -> Path | None:
    inicio = time.time()
    while time.time() - inicio < timeout:
        baixando = list(download_tmp.glob("*.crdownload"))
        novos = arquivos_pdf(download_tmp) - antes
        if novos and not baixando:
            return max(novos, key=lambda p: p.stat().st_mtime)
        time.sleep(0.5)
    return None


def esperar_resultados_ou_diagnostico(driver, wait, raiz: Path, timeout: int = 60) -> bool:
    """Espera linhas de resultado usando seletores variados."""
    seletores = [
        "table tbody tr",
        "tr.ng-scope",
        "tbody tr.ng-scope",
        "div.table-responsive tbody tr",
        "table tr",
    ]
    fim = time.time() + timeout
    while time.time() < fim:
        # captcha/erros comuns: não falha imediatamente; apenas aguarda.
        for seletor in seletores:
            try:
                linhas = driver.find_elements(By.CSS_SELECTOR, seletor)
                linhas_validas = [l for l in linhas if l.is_displayed() and len(l.find_elements(By.TAG_NAME, "td")) >= 2]
                if linhas_validas:
                    return True
            except Exception:
                pass
        time.sleep(1)
    salvar_debug(driver, raiz, "resultado não apareceu dentro do tempo limite")
    return False


def achar_campo_busca(driver, wait):
    seletores = [
        "input[placeholder*='Medicamento']",
        "input[placeholder*='medicamento']",
        "input[ng-model*='nomeProduto']",
        "input[name*='nomeProduto']",
        "input[type='text']",
    ]
    for seletor in seletores:
        try:
            return wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, seletor)))
        except Exception:
            continue
    raise TimeoutException("Campo de busca do medicamento não encontrado.")


def clicar_consultar_ou_enter(driver, campo):
    xpaths = [
        "//button[contains(translate(normalize-space(.), 'CONSULTAR', 'consultar'), 'consultar')]",
        "//input[@type='submit']",
        "//button[@type='submit']",
        "//button[contains(@class,'btn') and not(@disabled)]",
    ]
    for xp in xpaths:
        try:
            botao = driver.find_element(By.XPATH, xp)
            if botao.is_displayed() and botao.is_enabled():
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", botao)
                time.sleep(0.3)
                driver.execute_script("arguments[0].click();", botao)
                return
        except Exception:
            continue
    campo.send_keys(Keys.ENTER)


def pagina_parece_captcha(driver) -> bool:
    texto = (driver.page_source or "").lower()
    termos = ["captcha", "recaptcha", "não sou um robô", "nao sou um robo", "hcaptcha"]
    return any(t in texto for t in termos)


def aguardar_resultados_com_reloads(driver, raiz: Path, timeout_por_tentativa: int = 35, max_reloads: int = 3) -> bool:
    """Aguarda tabela; se não aparecer, recarrega. Se houver captcha, aguarda sem pedir ENTER."""
    for tentativa in range(1, max_reloads + 1):
        fim = time.time() + timeout_por_tentativa
        while time.time() < fim:
            if obter_linhas(driver):
                return True

            if pagina_parece_captcha(driver):
                print("  Captcha detectado. Resolva no navegador; o script continuará automaticamente.")
                # Espera maior, sem ENTER. Se o usuário resolver, a tabela aparece ou a página segue.
                limite_captcha = time.time() + 180
                while time.time() < limite_captcha:
                    if obter_linhas(driver):
                        return True
                    time.sleep(2)

            time.sleep(1)

        if tentativa < max_reloads:
            print(f"  Tabela não apareceu. Recarregando página ({tentativa}/{max_reloads - 1})...")
            try:
                driver.refresh()
                time.sleep(6)
            except Exception:
                pass

    salvar_debug(driver, raiz, "resultado não apareceu mesmo após recarregamentos")
    return False


def buscar_medicamento(driver, wait, medicamento: str, raiz: Path, max_reloads: int = 3):
    print(f"\n Consultando medicamento: {medicamento}")

    # Estratégia 1: abrir diretamente a rota de consulta já com o produto.
    url_direta = f"https://consultas.anvisa.gov.br/#/bulario/q/?nomeProduto={quote(medicamento)}"
    driver.get(url_direta)
    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
    time.sleep(5)

    if aguardar_resultados_com_reloads(driver, raiz, timeout_por_tentativa=25, max_reloads=max_reloads):
        return

    # Estratégia 2: tela inicial + busca pelo campo.
    print("  Rota direta falhou. Tentando busca pelo campo do formulário...")
    driver.get(BASE_URL)
    wait.until(lambda d: d.execute_script("return document.readyState") == "complete")
    time.sleep(5)

    campo = achar_campo_busca(driver, wait)
    campo.clear()
    campo.send_keys(medicamento)
    time.sleep(0.5)
    clicar_consultar_ou_enter(driver, campo)
    time.sleep(5)

    if not aguardar_resultados_com_reloads(driver, raiz, timeout_por_tentativa=35, max_reloads=max_reloads):
        raise TimeoutException("Tabela de resultados não carregou. Veja debug_anvisa.html e debug_anvisa.png.")

    time.sleep(2)

def obter_linhas(driver):
    seletores = ["table tbody tr", "tr.ng-scope", "tbody tr.ng-scope", "table tr"]
    for seletor in seletores:
        linhas = driver.find_elements(By.CSS_SELECTOR, seletor)
        linhas_validas = [l for l in linhas if l.is_displayed() and len(l.find_elements(By.TAG_NAME, "td")) >= 2]
        if linhas_validas:
            return linhas_validas
    return []


def obter_texto_coluna(colunas, indice: int) -> str:
    try:
        return colunas[indice].text.strip()
    except Exception:
        return ""


def obter_icones_pdf(linha):
    seletores = [
        "i.fa-file-pdf-o",
        "i.fa-file-pdf",
        "i[class*='pdf']",
        "img[src*='pdf']",
        "a[href*='pdf']",
        "a[ng-click*='bula']",
        "button[ng-click*='bula']",
        "a[ng-click*='visualizar']",
        "button[ng-click*='visualizar']",
    ]
    encontrados = []
    for seletor in seletores:
        try:
            encontrados.extend(linha.find_elements(By.CSS_SELECTOR, seletor))
        except Exception:
            pass
    unicos = []
    ids = set()
    for el in encontrados:
        if el.id not in ids:
            unicos.append(el)
            ids.add(el.id)
    return unicos


def identificar_tipo_pdf(icone, posicao: int) -> str:
    textos = []
    attrs = ["title", "alt", "aria-label", "tooltip", "uib-tooltip", "data-original-title", "ng-click", "href"]
    elementos = [icone]
    try:
        elementos.append(icone.find_element(By.XPATH, "./ancestor::*[self::a or self::button or self::td][1]"))
    except Exception:
        pass
    for el in elementos:
        for attr in attrs:
            try:
                v = el.get_attribute(attr)
                if v:
                    textos.append(v)
            except Exception:
                pass
        try:
            textos.append(el.text or "")
        except Exception:
            pass
    texto = " ".join(textos).lower()
    if "profissional" in texto or "prof" in texto:
        return "profissional"
    if "paciente" in texto:
        return "paciente"
    return "paciente" if posicao == 0 else "profissional"


def clicar_icone_pdf(driver, icone):
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", icone)
    time.sleep(0.4)
    driver.execute_script("arguments[0].click();", icone)


def fechar_abas_extras(driver):
    handles = driver.window_handles
    if not handles:
        return
    principal = handles[0]
    for handle in handles[1:]:
        try:
            driver.switch_to.window(handle)
            driver.close()
        except Exception:
            pass
    driver.switch_to.window(principal)


def mover_pdf(origem: Path, destino_dir: Path, nome_final: str) -> Path:
    destino = destino_dir / nome_final
    contador = 2
    while destino.exists():
        destino = destino_dir / nome_final.replace(".pdf", f"_{contador}.pdf")
        contador += 1
    shutil.move(str(origem), str(destino))
    return destino


def processar_pagina(driver, wait, pagina: int, download_tmp: Path, paciente_dir: Path, profissional_dir: Path):
    registros = []
    linhas = obter_linhas(driver)
    total = len(linhas)
    print(f"\nPágina {pagina}: {total} linha(s) encontrada(s).")

    for i in range(total):
        try:
            linhas = obter_linhas(driver)
            if i >= len(linhas):
                break
            linha = linhas[i]
            colunas = linha.find_elements(By.TAG_NAME, "td")
            medicamento_linha = obter_texto_coluna(colunas, 1) or obter_texto_coluna(colunas, 0)
            empresa = obter_texto_coluna(colunas, 2)
            expediente = obter_texto_coluna(colunas, 3)
            nome_base = normalizar_nome(f"{pagina:03d}_{i+1:03d}_{medicamento_linha}_{empresa}")

            icones = obter_icones_pdf(linha)
            if not icones:
                print(f"  {i+1}/{total} - sem ícones PDF: {medicamento_linha}")
                continue

            print(f"  {i+1}/{total} - {medicamento_linha} | PDFs encontrados: {len(icones)}")

            for posicao, icone in enumerate(icones[:2]):
                tipo = identificar_tipo_pdf(icone, posicao)
                destino_dir = paciente_dir if tipo == "paciente" else profissional_dir
                antes = arquivos_pdf(download_tmp)
                clicar_icone_pdf(driver, icone)
                time.sleep(1.2)
                fechar_abas_extras(driver)
                pdf_temp = aguardar_download_pdf(download_tmp, antes, timeout=90)

                if not pdf_temp:
                    print(f"    Falha: download não concluído para {tipo}.")
                    continue

                destino = mover_pdf(pdf_temp, destino_dir, f"{nome_base}_{tipo}.pdf")
                registros.append({
                    "pagina": pagina,
                    "linha": i + 1,
                    "medicamento": medicamento_linha,
                    "empresa": empresa,
                    "expediente": expediente,
                    "tipo_bula": tipo,
                    "arquivo": str(destino),
                    "data_download": datetime.now().isoformat(timespec="seconds"),
                })
                print(f"    Baixado: {tipo} -> {destino.name}")

        except StaleElementReferenceException:
            print(f"  Linha {i+1}: elemento obsoleto. Pulando.")
        except Exception as erro:
            print(f"  Linha {i+1}: erro: {erro}")

    return registros


def ir_para_proxima_pagina(driver, wait) -> bool:
    candidatos_xpath = [
        "//a[contains(normalize-space(.), '»')]",
        "//li[not(contains(@class,'disabled'))]/a[contains(normalize-space(.), '»')]",
        "//a[contains(translate(normalize-space(.), 'PRÓXIMAÓ', 'próximaó'), 'próxima')]",
        "//button[contains(translate(normalize-space(.), 'PRÓXIMAÓ', 'próximaó'), 'próxima')]",
    ]
    for xpath in candidatos_xpath:
        try:
            btn = driver.find_element(By.XPATH, xpath)
            classe_btn = (btn.get_attribute("class") or "").lower()
            classe_pai = ""
            try:
                classe_pai = (btn.find_element(By.XPATH, "./..").get_attribute("class") or "").lower()
            except Exception:
                pass
            if "disabled" in classe_btn or "disabled" in classe_pai:
                return False
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            time.sleep(0.4)
            driver.execute_script("arguments[0].click();", btn)
            time.sleep(4)
            return len(obter_linhas(driver)) > 0
        except NoSuchElementException:
            continue
        except Exception:
            continue
    return False


def salvar_csv(caminho_csv: Path, registros: list[dict]):
    if not registros:
        return
    campos = ["pagina", "linha", "medicamento", "empresa", "expediente", "tipo_bula", "arquivo", "data_download"]
    with open(caminho_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=campos)
        writer.writeheader()
        writer.writerows(registros)


def ler_medicamentos_de_arquivo(caminho: str) -> list[str]:
    path = Path(caminho)
    if not path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {caminho}")

    meds: list[str] = []
    texto = path.read_text(encoding="utf-8-sig", errors="ignore")

    # Aceita TXT com um medicamento por linha ou CSV simples com separador ; ou ,.
    for linha in texto.splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue
        partes = re.split(r"[;,]", linha)
        candidato = partes[0].strip()
        if candidato.lower() in {"medicamento", "nome", "nomeproduto", "nome_produto"}:
            continue
        if candidato:
            meds.append(candidato)
    return meds


def montar_lista_medicamentos(args) -> list[str]:
    meds: list[str] = []
    if args.medicamento:
        meds.append(args.medicamento)
    if args.medicamentos:
        meds.extend(args.medicamentos)
    if args.arquivo_medicamentos:
        meds.extend(ler_medicamentos_de_arquivo(args.arquivo_medicamentos))

    # Remove duplicados preservando ordem.
    unicos = []
    vistos = set()
    for med in meds:
        med = (med or "").strip()
        chave = med.lower()
        if med and chave not in vistos:
            unicos.append(med)
            vistos.add(chave)
    return unicos


def limpar_tmp(download_tmp: Path):
    download_tmp.mkdir(parents=True, exist_ok=True)
    for arq in download_tmp.glob("*"):
        try:
            if arq.is_file():
                arq.unlink()
            elif arq.is_dir():
                shutil.rmtree(arq)
        except Exception:
            pass


def processar_medicamento(driver, wait, medicamento: str, args, download_tmp: Path) -> list[dict]:
    raiz, _, paciente_dir, profissional_dir = criar_estrutura_saida(args.saida, medicamento)
    limpar_tmp(download_tmp)

    todos: list[dict] = []
    try:
        buscar_medicamento(driver, wait, medicamento, raiz, max_reloads=args.max_reloads)
        pagina = 1
        while True:
            registros = processar_pagina(driver, wait, pagina, download_tmp, paciente_dir, profissional_dir)
            for r in registros:
                r["consulta"] = medicamento
            todos.extend(registros)
            salvar_csv_medicamento(raiz / "controle_downloads.csv", todos)

            if args.max_paginas and pagina >= args.max_paginas:
                print("  Limite de páginas atingido para este medicamento.")
                break
            if not ir_para_proxima_pagina(driver, wait):
                print(" Fim da paginação para este medicamento.")
                break
            pagina += 1
    except Exception as erro:
        print(f"Falha ao processar {medicamento}: {erro}")
        try:
            salvar_debug(driver, raiz, str(erro))
        except Exception:
            pass

    print(f"Finalizado: {medicamento} | PDFs baixados: {len(todos)}")
    return todos


def salvar_csv_medicamento(caminho_csv: Path, registros: list[dict]):
    if not registros:
        return
    campos = ["consulta", "pagina", "linha", "medicamento", "empresa", "expediente", "tipo_bula", "arquivo", "data_download"]
    with open(caminho_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=campos)
        writer.writeheader()
        writer.writerows(registros)


def salvar_csv_geral(caminho_csv: Path, registros: list[dict]):
    if not registros:
        return
    caminho_csv.parent.mkdir(parents=True, exist_ok=True)
    campos = ["consulta", "pagina", "linha", "medicamento", "empresa", "expediente", "tipo_bula", "arquivo", "data_download"]
    with open(caminho_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=campos)
        writer.writeheader()
        writer.writerows(registros)


def main():
    parser = argparse.ArgumentParser(description="Baixar bulas do paciente e do profissional no Bulário ANVISA.")
    parser.add_argument("--medicamento", default=None, help="Nome de um medicamento para consulta.")
    parser.add_argument("--medicamentos", nargs="*", default=None, help="Lista de medicamentos separados por espaço.")
    parser.add_argument("--arquivo-medicamentos", default=None, help="Arquivo .txt ou .csv com um medicamento por linha.")
    parser.add_argument("--saida", default="bulas", help="Diretório base de saída.")
    parser.add_argument("--max-paginas", type=int, default=0, help="Limite de páginas por medicamento. Use 0 para todas.")
    parser.add_argument("--max-reloads", type=int, default=3, help="Quantidade de recarregamentos quando a tabela não aparecer.")
    parser.add_argument("--headless", action="store_true", help="Executar sem janela. Não recomendado se houver captcha.")
    args = parser.parse_args()

    medicamentos = montar_lista_medicamentos(args)
    if not medicamentos:
        medicamentos = ["dipirona"]

    print("\nMedicamentos na fila:")
    for idx, med in enumerate(medicamentos, 1):
        print(f"  {idx}. {med}")

    base_saida = Path(args.saida)
    download_tmp = base_saida / "_downloads_tmp_global"
    download_tmp.mkdir(parents=True, exist_ok=True)

    driver = configurar_driver(download_tmp, headless=args.headless)
    wait = WebDriverWait(driver, 60)
    geral: list[dict] = []

    try:
        for pos, medicamento in enumerate(medicamentos, 1):
            print(f"\n==============================")
            print(f"Medicamento {pos}/{len(medicamentos)}: {medicamento}")
            print(f"==============================")
            registros = processar_medicamento(driver, wait, medicamento, args, download_tmp)
            geral.extend(registros)
            salvar_csv_geral(base_saida / "controle_downloads_geral.csv", geral)
    finally:
        try:
            driver.quit()
        except Exception:
            pass
        try:
            if download_tmp.exists() and not any(download_tmp.iterdir()):
                download_tmp.rmdir()
        except Exception:
            pass

    print("\nProcesso finalizado.")
    print(f"CSV geral: {base_saida / 'controle_downloads_geral.csv'}")
    print(f"Total de PDFs baixados: {len(geral)}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.")
        sys.exit(130)

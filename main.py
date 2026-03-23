import os
import json
import requests
import time
import random
from concurrent.futures import ThreadPoolExecutor
from DrissionPage import ChromiumPage
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeRemainingColumn, MofNCompleteColumn

console = Console()
HEADERS = {
    'Referer': 'https://mangalib.org/',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
}
BASE_IMG_URL = "https://img3.mixlib.me"

def get_chapters_list(url):
    with console.status("[bold blue]Получаю полный список глав...[/bold blue]", spinner="dots"):
        page = ChromiumPage()
        page.listen.start('api/manga/') 
        page.get(url)
        
        chapters = []
        for res in page.listen.steps():
            data = res.response.body
            if isinstance(data, str): data = json.loads(data)
            inner = data.get('data', data)
            # Ищем массив, где есть объекты с полем 'number'
            if isinstance(inner, list) and len(inner) > 0 and 'number' in inner[0]:
                chapters = inner
                break
        page.quit()
        return chapters

def get_pages_for_chapter(page: ChromiumPage, chapter_url: str):
    page.listen.start('api/manga/')
    page.get(chapter_url)
    
    for res in page.listen.steps(timeout=5):
        try:
            data = res.response.body
            if isinstance(data, str): data = json.loads(data)
            inner = data.get('data', data)
            if isinstance(inner, dict) and 'pages' in inner:
                return inner['pages'], inner.get('number', '0')
        except: continue
    return [], "0"

session = requests.Session()
session.headers.update(HEADERS)

def download_file(args):
    url, path = args
    # Если файл уже есть и он не пустой (защита от перезаписи при перезапуске)
    if os.path.exists(path) and os.path.getsize(path) > 1024:
        return True
        
    retries = 5
    for attempt in range(retries):
        try:
            # Имитация человеческой загрузки (отклонение от ритма)
            time.sleep(random.uniform(0.1, 0.4))
            
            res = session.get(url, timeout=15)
            if res.status_code == 200 and len(res.content) > 1024:
                with open(path, 'wb') as f:
                    f.write(res.content)
                return True
            elif res.status_code == 429:  # Too Many Requests (лимит)
                time.sleep(2 + attempt)  # Прогрессивный таймаут
            else:
                time.sleep(1)
        except:
            time.sleep(1)
    return False

def build_reader_url(manga_base_url: str, vol: str, num: str) -> str:
    """Генерирует правильную ссылку на читалку, удаляя '/manga/' из пути"""
    reader_base_url = manga_base_url.replace('/manga/', '/')
    return f"{reader_base_url}/read/v{vol}/c{num}"

def main():
    console.print(Panel.fit("[bold magenta]MANGA-LIB BULK DOWNLOADER[/bold magenta]\n[cyan]v1.3 | Full List & Link Fix[/cyan]", border_style="cyan"))

    manga_url_input = Prompt.ask("[bold yellow]Вставьте ссылку на тайтл[/bold yellow]")
    manga_base_url = manga_url_input.split('?')[0].rstrip('/')
    
    try:
        all_chapters = get_chapters_list(manga_url_input)
        if not all_chapters:
            console.print("[red]Ошибка: Список глав пуст.[/red]")
            return

        # Сортируем: сначала по тому, потом по номеру главы
        all_chapters.sort(key=lambda x: (float(x.get('volume', 0)), float(x.get('number', 0))))

        # Выводим ВСЕ главы
        table = Table(title=f"Найдено глав: {len(all_chapters)}")
        table.add_column("ID (для ввода)", style="cyan", justify="center")
        table.add_column("Том", style="magenta")
        table.add_column("Глава", style="green")
        
        for index, ch in enumerate(all_chapters):
            table.add_row(str(index), str(ch.get('volume', '?')), str(ch.get('number', '?')))
        
        console.print(table)

        selection = Prompt.ask("[bold white]Введите диапазон (напр. 0-10) или номера через запятую (0,2,5)[/bold white]")

        selected_chapters = []
        if '-' in selection:
            start, end = map(int, selection.split('-'))
            selected_chapters = all_chapters[start:end+1]
        else:
            indices = [int(i.strip()) for i in selection.split(',')]
            selected_chapters = [all_chapters[i] for i in indices]

        all_download_tasks = []
        
        with console.status("[bold blue]Инициализация браузера для сбора страниц...[/bold blue]", spinner="dots"):
            page = ChromiumPage()
            page.listen.start('api/manga/')

        # Очередь для сбора страниц
        chapters_to_parse = selected_chapters.copy()
        missed_chapters = []

        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), BarColumn(), TaskProgressColumn(), MofNCompleteColumn(), TimeRemainingColumn(), console=console) as parse_progress:
            parse_task = parse_progress.add_task("[blue]Парсинг глав...", total=len(chapters_to_parse))

            while chapters_to_parse:
                ch = chapters_to_parse.pop(0)
                vol = ch.get('volume')
                num = ch.get('number')
                
                # Формируем правильную ссылку для читалки
                chapter_read_url = build_reader_url(manga_base_url, vol, num)
                
                parse_progress.update(parse_task, description=f"[blue]Сбор ссылок: Глава {num} (Том {vol})")
                
                pages, actual_num = get_pages_for_chapter(page, chapter_read_url)
                
                if not pages:
                    parse_progress.console.print(f"[red]Сработал лимит на главе {num}. Глава добавлена в конец очереди на повтор.[/red]")
                    missed_chapters.append(ch)
                    # Если пропустили, делаем чуть большую паузу перед следующим запросом
                    time.sleep(random.uniform(5.0, 8.0))
                else:
                    # Папка: название_манги/vol_X_ch_Y
                    manga_slug = manga_base_url.split('/')[-1]
                    folder = f"downloads/{manga_slug}/v{vol}_c{num}"
                    os.makedirs(folder, exist_ok=True)
                    for i, p in enumerate(pages):
                        url_part = p['url']
                        if url_part.startswith('//'): url_part = url_part[1:]
                        f_url = BASE_IMG_URL + (url_part if url_part.startswith('/') else '/' + url_part)
                        ext = url_part.split('.')[-1].split('?')[0] # Очистка расширения
                        all_download_tasks.append((f_url, os.path.join(folder, f"{i+1:03d}.{ext}")))
                    
                    parse_progress.advance(parse_task)
                    time.sleep(random.uniform(2.5, 5.0))

            if not chapters_to_parse and missed_chapters:
                parse_progress.console.print(f"[yellow]Начинаю повторный сбор пропущенных глав ({len(missed_chapters)} шт.)... Нажмите Ctrl+C, если хотите прервать парсинг.[/yellow]")
                chapters_to_parse = missed_chapters.copy()
                missed_chapters.clear()
                # Увеличим общий прогресс бар (можно также пересоздать)
                # Прибавлять к total не нужно, так как advance вызывался только при успешном парсинге
                # Просто ждем чуть дольше перед новым циклом, чтобы блокировка спала
                time.sleep(10.0)

        # Синхронизируем состояние бота с реальным браузером,
        # чтобы CDN воспринимал скачивание так, будто это мы читаем с сайта
        try:
            browser_ua = page.run_js("return navigator.userAgent;")
            session.headers.update({'User-Agent': browser_ua})
            for cookie in page.cookies():
                session.cookies.set(cookie['name'], cookie['value'], domain=cookie.get('domain', ''))
        except Exception:
            pass

        page.quit()

        if not all_download_tasks:
            console.print("[red]Нет страниц для скачивания.[/red]")
            return

        console.print(f"\n[bold yellow]Найдено {len(all_download_tasks)} страниц. Начинаю массовую загрузку...[/bold yellow]")
        
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), BarColumn(), TaskProgressColumn(), MofNCompleteColumn(), TimeRemainingColumn(), console=console) as progress:
            task = progress.add_task("[white]Массовая загрузка страниц...", total=len(all_download_tasks))
            # Уменьшили потоки до 12, чтобы не ловить бан (Error 429), и используем сессию
            with ThreadPoolExecutor(max_workers=12) as executor:
                for _ in executor.map(download_file, all_download_tasks):
                    progress.advance(task)
            
        console.print("\n[bold rgb(0,255,0)]🚀 ВСЕ ВЫБРАННЫЕ ГЛАВЫ УСПЕШНО ЗАГРУЖЕНЫ![/bold rgb(0,255,0)]")

    except Exception as e:
        console.print(f"[bold red]Ошибка программы:[/bold red] {e}")

if __name__ == "__main__":
    main()

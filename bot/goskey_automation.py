"""
Автоматизация приложения Госключ через uiautomator2.

ВАЖНО: конкретные resourceId/тексты элементов ниже — ЗАГЛУШКИ (TODO).
Я не могу их узнать без доступа к реальному приложению. Чтобы найти
настоящие значения:

    1. Откройте Госключ в контейнере (через scrcpy, см. README п.4).
    2. Запустите `weditor` (pip install weditor && weditor,
       http://localhost:17310) и подключитесь к 127.0.0.1:5555.
    3. Кликните на список документов / на отдельный договор — в правой
       панели weditor покажет resourceId, text, className элемента.
    4. Впишите найденные значения вместо заглушек ниже.

Тот же дамп можно получить и без GUI:
    python -c "import uiautomator2 as u2; print(u2.connect('127.0.0.1:5555').dump_hierarchy())"
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import uiautomator2 as u2

log = logging.getLogger(__name__)

GOSKEY_PACKAGE = "ru.goskey"  # TODO: замените на настоящий package name приложения
# Узнать так: adb shell pm list packages | grep -i goskey (после установки APK)


@dataclass
class Document:
    doc_id: str  # любой стабильный идентификатор — дата+название, если нет явного id
    title: str


class GoskeyAutomation:
    def __init__(self, adb_device: str):
        self._d = u2.connect(adb_device)

    def dump_ui(self) -> str:
        """Утилита для отладки — сохраняет текущее дерево экрана в XML."""
        return self._d.dump_hierarchy()

    def ensure_app_open(self) -> None:
        self._d.app_start(GOSKEY_PACKAGE, stop=False)
        self._d.implicitly_wait(10)
        # TODO: дождаться конкретного элемента главного экрана вместо app_current
        self._d.wait_activity(".MainActivity", timeout=15)  # TODO: реальное имя activity

    def is_logged_in(self) -> bool:
        """
        Проверка, что мы не на экране входа (сессия жива).

        TODO: замените на реальный признак экрана логина — например,
        наличие кнопки "Войти через Госуслуги" или поля ввода пароля.
        Сейчас это грубая заглушка по слову "Войти".
        """
        login_marker = self._d(textContains="Войти")  # TODO: точный текст/resourceId кнопки входа
        return not login_marker.exists

    def is_login_form(self) -> bool:
        """
        Экран ввода логина+пароля (первый шаг входа, до кода из SMS).

        TODO: замените на реальный признак — например, наличие двух полей
        EditText (логин/телефон и пароль) на экране входа.
        """
        return self._d(textContains="Войти").exists and self._d(className="android.widget.EditText").count >= 2

    def fill_login_form(self, login: str, password: str) -> None:
        # TODO: реальные resourceId полей логина/пароля и кнопки "Войти"
        self._d(resourceId=f"{GOSKEY_PACKAGE}:id/login_input").set_text(login)
        self._d(resourceId=f"{GOSKEY_PACKAGE}:id/password_input").set_text(password)
        self._d(resourceId=f"{GOSKEY_PACKAGE}:id/btn_login").click()
        self._d.sleep(2)

    def is_awaiting_otp(self) -> bool:
        """
        Экран ввода кода из SMS (второй шаг входа).

        TODO: замените на реальный признак — например, поле EditText с
        hint'ом "Код из СМС" или конкретный resourceId экрана OTP.
        """
        return self._d(textContains="код").exists and self._d(className="android.widget.EditText").count == 1

    def submit_otp(self, code: str) -> None:
        # TODO: реальные resourceId поля кода и кнопки подтверждения
        self._d(resourceId=f"{GOSKEY_PACKAGE}:id/otp_input").set_text(code)
        self._d(resourceId=f"{GOSKEY_PACKAGE}:id/btn_confirm").click()
        self._d.sleep(2)

    def open_documents_list(self) -> None:
        # TODO: заменить на реальный селектор вкладки/пункта меню "Документы"
        self._d(text="Документы").click()
        self._d(resourceId=f"{GOSKEY_PACKAGE}:id/documents_list").wait(timeout=10)

    def list_documents(self) -> list[Document]:
        """Возвращает список документов, видимых на экране списка."""
        self.open_documents_list()

        # TODO: замените resourceId на реальный id элемента-карточки документа
        items = self._d(resourceId=f"{GOSKEY_PACKAGE}:id/doc_item")

        docs: list[Document] = []
        for i in range(items.count):
            item = items[i]
            # TODO: замените на реальные resourceId полей заголовка/даты внутри карточки
            title_el = item.child(resourceId=f"{GOSKEY_PACKAGE}:id/doc_title")
            title = title_el.get_text() if title_el.exists else f"document_{i}"
            docs.append(Document(doc_id=title, title=title))
        return docs

    def download_document(self, doc: Document, download_dir: str) -> str:
        """
        Открывает документ и сохраняет его как PDF в download_dir.
        Возвращает путь к файлу.

        TODO: два реальных способа получить файл, выберите после инспекции приложения:
          A) Если в приложении есть кнопка "Скачать"/"Поделиться" — она обычно
             сохраняет файл в /sdcard/Download внутри контейнера, откуда его
             можно забрать через `adb pull`.
          B) Если файла нет в файловой системе (documento рендерится прямо в
             приложении) — остаётся вариант со скриншотами страниц документа
             (см. заглушку _screenshot_fallback ниже).
        """
        self._d(text=doc.title).click()
        # TODO: нажать реальную кнопку скачивания/экспорта
        self._d(resourceId=f"{GOSKEY_PACKAGE}:id/btn_download").click()
        self._d.sleep(2)

        remote_path = "/sdcard/Download"  # TODO: уточнить реальный путь сохранения
        local_path = str(Path(download_dir) / f"{doc.doc_id}.pdf")
        self._d.pull(remote_path, local_path)  # TODO: указать точное имя файла, не папку

        self._d.press("back")
        return local_path

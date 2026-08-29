package ru.forctrl.ruvpn

import android.content.Context
import android.util.Base64
import com.wireguard.config.Config
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Ключ доступа — который выдаёт телеграм-бот: 8 букв и цифр, без всякого
 * `ruvpn://` на виду (бот присылает голый код). Пользователю не нужно
 * знать, что внутри — он копирует строку из чата и вставляет её в
 * приложении одной кнопкой; префикс `ruvpn://` — деталь только для самого
 * приложения (внутри понимается и с ним, и без — код распознаётся по
 * формату) и для обратной совместимости со старым форматом ключа.
 *
 * Сам код короткий (8 букв и цифр) — настоящие настройки тунеля в него не
 * помещаются, поэтому при вставке приложение один раз спрашивает их у
 * сервера ключей ([resolve]) и сохраняет уже готовый конфиг — дальше
 * подключение работает офлайн, без обращений к серверу ключей.
 *
 * Старый формат (весь конфиг, упакованный в base64 в строке с префиксом
 * `ruvpn://`) тоже понимается — на случай ключа, выданного до перехода на
 * короткие коды.
 *
 * Откуда берём ключ, по приоритету:
 *   1. тот, что человек вставил в приложении (уже разрешённый в конфиг);
 *   2. вшитый в сборку (assets/wg.conf), если APK собирали под себя.
 */
object KeyStore {

    const val PREFIX = "ruvpn://"

    private const val SAVED_FILE = "key.txt"
    private const val BUNDLED_ASSET = "wg.conf"

    private fun savedFile(context: Context) = File(context.filesDir, SAVED_FILE)

    fun rawText(context: Context): String? {
        val saved = savedFile(context)
        if (saved.exists()) {
            return saved.readText().takeIf { it.isNotBlank() }
        }
        return runCatching {
            context.assets.open(BUNDLED_ASSET).use { it.readBytes().toString(Charsets.UTF_8) }
        }.getOrNull()?.takeIf { it.isNotBlank() }
    }

    fun isOwn(context: Context): Boolean = savedFile(context).exists()

    fun save(context: Context, text: String) {
        savedFile(context).writeText(text.trim())
    }

    /**
     * Тестовый ключ — только чтобы посмотреть анимацию кнопки, без
     * настоящего подключения. Не конфиг тунеля, поэтому [parse] на нём
     * закономерно падает — [MainActivity] проверяет [isDemo] раньше, чем
     * дойти до реального конфига.
     */
    const val DEMO_KEY = "test1590"
    private const val DEMO_SENTINEL = "demo:$DEMO_KEY"

    fun isDemoKey(text: String): Boolean {
        val value = text.trim()
        val payload = if (value.startsWith(PREFIX, ignoreCase = true)) {
            value.substring(PREFIX.length).trim()
        } else {
            value
        }
        return payload.equals(DEMO_KEY, ignoreCase = true)
    }

    fun saveDemo(context: Context) {
        savedFile(context).writeText(DEMO_SENTINEL)
    }

    fun isDemo(context: Context): Boolean = rawText(context) == DEMO_SENTINEL

    /** Быстрая проверка «похоже на ключ» — чтобы подставлять из буфера обмена. */
    fun looksLikeKey(text: String?): Boolean {
        val value = text?.trim() ?: return false
        if (value.startsWith(PREFIX, ignoreCase = true) || value.contains("[Interface]")) {
            return true
        }
        return isShortCode(value)
    }

    /** Короткий код (новый формат) отличается от старого base64-блока длиной и алфавитом. */
    private fun isShortCode(payload: String): Boolean =
        payload.length in 4..16 && payload.all { it.isLetterOrDigit() }

    /**
     * Превращает вставленный текст в настоящий конфиг тунеля: короткий код —
     * запросом к серверу ключей, старый формат или сырой конфиг — на месте.
     * Результат уже готов для [parse] и [save]. Код принимается и голым
     * (как теперь присылает бот), и со старым префиксом `ruvpn://`.
     */
    suspend fun resolve(text: String): String {
        val value = text.trim()
        if (!value.startsWith(PREFIX, ignoreCase = true)) {
            return if (isShortCode(value)) fetchConfig(value) else value
        }
        val payload = value.substring(PREFIX.length).trim()
        return if (isShortCode(payload)) fetchConfig(payload) else decodeLegacy(payload)
    }

    private suspend fun fetchConfig(code: String): String = withContext(Dispatchers.IO) {
        val base = BuildConfig.KEY_SERVER_URL.trimEnd('/')
        check(base.isNotBlank()) { "адрес сервера ключей не задан в этой сборке" }
        val connection = (URL("$base/key/$code").openConnection() as HttpURLConnection).apply {
            connectTimeout = 10_000
            readTimeout = 10_000
            requestMethod = "GET"
        }
        try {
            check(connection.responseCode == 200) { "сервер ответил ${connection.responseCode}" }
            connection.inputStream.bufferedReader().use { it.readText() }
        } finally {
            connection.disconnect()
        }
    }

    /** Старый формат ключа: весь конфиг, упакованный в base64 прямо в строке. */
    private fun decodeLegacy(payload: String): String {
        val normalized = payload
            .filterNot { it.isWhitespace() }
            .replace('-', '+')
            .replace('_', '/')
        val bytes = Base64.decode(normalized, Base64.DEFAULT)
        return String(bytes, Charsets.UTF_8)
    }

    /** Разбирает уже разрешённый (см. [resolve]) текст настроек в конфиг тунеля. */
    fun parse(text: String): Config = Config.parse(text.byteInputStream(Charsets.UTF_8))
}

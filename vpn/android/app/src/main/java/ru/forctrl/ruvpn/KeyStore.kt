package ru.forctrl.ruvpn

import android.content.Context
import android.util.Base64
import com.wireguard.config.Config
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
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
 *
 * Отдельно хранится [Meta] — код и выбранная страна: только для тех
 * ключей, что пришли коротким кодом от бота (для вставленного вручную
 * конфига или старого base64-ключа кода нет, и смена страны недоступна —
 * см. [hasSwitchableCode]).
 */
object KeyStore {

    const val PREFIX = "ruvpn://"

    private const val SAVED_FILE = "key.txt"
    private const val META_FILE = "key_meta.json"
    private const val BUNDLED_ASSET = "wg.conf"

    const val DEFAULT_COUNTRY = "ru"

    private fun savedFile(context: Context) = File(context.filesDir, SAVED_FILE)
    private fun metaFile(context: Context) = File(context.filesDir, META_FILE)

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

    /**
     * [code] — короткий код, которым это было получено (если получено им);
     * null — вставили готовый конфиг или старый base64-ключ, смена страны
     * для такого недоступна. Страна новой сохранённой записи всегда
     * [DEFAULT_COUNTRY] — переключают её позже, отдельно, через [switchCountry].
     */
    fun save(context: Context, text: String, code: String?) {
        savedFile(context).writeText(text.trim())
        writeMeta(context, code, DEFAULT_COUNTRY)
        // Новый основной ключ — значит, подключаться им, а не своим сервером.
        selectServer(context, null)
    }

    /** Тестовый ключ — только чтобы посмотреть анимацию кнопки, без
     * настоящего подключения. Не конфиг тунеля, поэтому [parse] на нём
     * закономерно падает — [MainActivity] проверяет [isDemo] раньше, чем
     * дойти до реального конфига. */
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
        writeMeta(context, code = null, country = DEFAULT_COUNTRY)
        selectServer(context, null)
    }

    fun isDemo(context: Context): Boolean =
        servers(context).selected == null && rawText(context) == DEMO_SENTINEL

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

    /** Результат [resolve]: готовый конфиг и код, которым он получен (null — если
     * это был вставленный вручную конфиг или старый base64-ключ, без короткого кода). */
    data class Resolved(val text: String, val code: String?)

    /**
     * Превращает вставленный текст в настоящий конфиг тунеля: короткий код —
     * запросом к серверу ключей (страна — всегда [DEFAULT_COUNTRY], смена
     * страны — уже после сохранения, через [switchCountry]), старый формат
     * или сырой конфиг — на месте. Код принимается и голым (как теперь
     * присылает бот), и со старым префиксом `ruvpn://`.
     */
    suspend fun resolve(text: String): Resolved {
        val value = text.trim()
        if (!value.startsWith(PREFIX, ignoreCase = true)) {
            return if (isShortCode(value)) {
                Resolved(fetchConfig(value, DEFAULT_COUNTRY), value)
            } else {
                Resolved(value, null)
            }
        }
        val payload = value.substring(PREFIX.length).trim()
        return if (isShortCode(payload)) {
            Resolved(fetchConfig(payload, DEFAULT_COUNTRY), payload)
        } else {
            Resolved(decodeLegacy(payload), null)
        }
    }

    /**
     * Текущая выбранная страна ([DEFAULT_COUNTRY], если ключ не менял
     * страну ни разу или её нельзя сменить).
     */
    fun currentCountry(context: Context): String = readMeta(context)?.country ?: DEFAULT_COUNTRY

    /** Доступна ли смена страны для сохранённого ключа — только для тех, что пришли коротким кодом. */
    fun hasSwitchableCode(context: Context): Boolean = readMeta(context)?.code != null

    /**
     * Меняет сервер (страну) для уже сохранённого ключа: тот же самый код,
     * что уже ввели, просто переспрашивается у сервера ключей с другой
     * страной. Возвращает false, если у сохранённого ключа нет кода (значит,
     * это вставленный вручную конфиг) — тогда снаружи ничего не менялось.
     */
    suspend fun switchCountry(context: Context, country: String): Boolean {
        val meta = readMeta(context) ?: return false
        val code = meta.code ?: return false
        if (meta.country != country) {
            val text = fetchConfig(code, country)
            parse(text) // проверка, что сервер отдал настоящий конфиг тунеля
            savedFile(context).writeText(text.trim())
            writeMeta(context, code, country)
        }
        // Выбор страны — это выбор основного ключа.
        selectServer(context, null)
        return true
    }

    /**
     * Статус ключа на собственном сервере ключей: "active", "revoked",
     * "unknown" (сеть, таймаут, старый сервер без /status, любой
     * неожиданный ответ) или "unmanaged" (ключ не от бота / выбран свой
     * сервер). Отключать туннель можно только по явному "revoked" — сбой
     * сервиса проверки IP ничего не говорит о ключе.
     */
    suspend fun keyStatus(context: Context): String {
        if (servers(context).selected != null) return "unmanaged"
        val meta = readMeta(context) ?: return "unmanaged"
        val code = meta.code?.takeIf { isShortCode(it) } ?: return "unmanaged"
        val base = BuildConfig.KEY_SERVER_URL.trimEnd('/')
        if (base.isBlank()) return "unknown"
        return withContext(Dispatchers.IO) {
            runCatching {
                val connection =
                    (URL("$base/key/$code/status?country=${meta.country}").openConnection() as HttpURLConnection).apply {
                        connectTimeout = 10_000
                        readTimeout = 10_000
                        requestMethod = "GET"
                    }
                try {
                    if (connection.responseCode != 200) {
                        "unknown"
                    } else {
                        val body = connection.inputStream.bufferedReader().use { it.readText() }
                        when (JSONObject(body).optString("status")) {
                            "active" -> "active"
                            "revoked" -> "revoked"
                            else -> "unknown"
                        }
                    }
                } finally {
                    connection.disconnect()
                }
            }.getOrDefault("unknown")
        }
    }

    // --- свои серверы рядом с основным ключом --------------------------------

    /**
     * Дополнительные серверы (например, запасной): полные конфиги WireGuard,
     * между которыми можно переключаться в окне «Сервер». Основной ключ
     * (key.txt) при этом остаётся как есть. [Servers.selected] = null —
     * подключаемся основным ключом.
     */
    data class Server(val id: String, val name: String, val config: String)

    data class Servers(val selected: String?, val list: List<Server>)

    private const val SERVERS_FILE = "servers.json"

    private fun serversFile(context: Context) = File(context.filesDir, SERVERS_FILE)

    fun servers(context: Context): Servers {
        val file = serversFile(context)
        if (!file.exists()) return Servers(null, emptyList())
        return runCatching {
            val json = JSONObject(file.readText())
            val array = json.optJSONArray("servers") ?: JSONArray()
            val list = (0 until array.length()).mapNotNull { index ->
                val item = array.optJSONObject(index) ?: return@mapNotNull null
                Server(item.optString("id"), item.optString("name"), item.optString("config"))
                    .takeIf { it.id.isNotBlank() && it.config.isNotBlank() }
            }
            // Выбранный сервер, которого уже нет, означает «основной ключ».
            val selected = json.optString("selected").takeIf { id -> list.any { it.id == id } }
            Servers(selected, list)
        }.getOrDefault(Servers(null, emptyList()))
    }

    private fun writeServers(context: Context, servers: Servers) {
        val array = JSONArray()
        servers.list.forEach {
            array.put(JSONObject().put("id", it.id).put("name", it.name).put("config", it.config))
        }
        val json = JSONObject()
            .put("selected", servers.selected ?: JSONObject.NULL)
            .put("servers", array)
        serversFile(context).writeText(json.toString())
    }

    /** Добавляет проверенный конфиг и сразу выбирает его; тот же конфиг второй раз не дублируется. */
    fun addServer(context: Context, text: String): Server {
        val trimmed = text.trim()
        val config = parse(trimmed) // неверный конфиг — исключение, список не трогаем
        val current = servers(context)
        current.list.firstOrNull { it.config == trimmed }?.let { existing ->
            writeServers(context, current.copy(selected = existing.id))
            return existing
        }
        val base = hostName(config) ?: "Сервер"
        var name = base
        var n = 2
        while (current.list.any { it.name == name }) {
            name = "$base ($n)"
            n++
        }
        val server = Server(System.currentTimeMillis().toString(), name, trimmed)
        writeServers(context, Servers(server.id, current.list + server))
        return server
    }

    /** id = null — снова основной ключ. */
    fun selectServer(context: Context, id: String?) {
        val current = servers(context)
        if (current.list.isEmpty() && id == null) return
        val selected = id?.takeIf { wanted -> current.list.any { it.id == wanted } }
        writeServers(context, current.copy(selected = selected))
    }

    /** true — удалили выбранный сервер, подключение перешло на основной ключ. */
    fun removeServer(context: Context, id: String): Boolean {
        val current = servers(context)
        val wasSelected = current.selected == id
        writeServers(
            context,
            Servers(if (wasSelected) null else current.selected, current.list.filterNot { it.id == id }),
        )
        return wasSelected
    }

    /** Текст конфига, которым подключаться сейчас: свой выбранный сервер или основной ключ. */
    fun activeText(context: Context): String? {
        val current = servers(context)
        return current.list.firstOrNull { it.id == current.selected }?.config ?: rawText(context)
    }

    /** Есть ли настоящий основной ключ (не тестовый). */
    fun hasMainKey(context: Context): Boolean =
        rawText(context)?.let { it != DEMO_SENTINEL } == true

    /** «Сервер <адрес>» для основного ключа, вставленного готовым конфигом. */
    fun mainName(context: Context): String? {
        val text = rawText(context)?.takeIf { it != DEMO_SENTINEL } ?: return null
        return runCatching { hostName(parse(text)) }.getOrNull()
    }

    private fun hostName(config: Config): String? =
        config.peers.firstOrNull()?.endpoint?.orElse(null)?.host
            ?.takeIf { it.isNotBlank() }
            ?.let { "Сервер $it" }

    private data class Meta(val code: String?, val country: String)

    private fun readMeta(context: Context): Meta? {
        val file = metaFile(context)
        if (!file.exists()) return null
        return runCatching {
            val json = JSONObject(file.readText())
            Meta(
                code = json.optString("code").takeIf { it.isNotBlank() },
                country = json.optString("country", DEFAULT_COUNTRY)
                    .takeIf { it.isNotBlank() } ?: DEFAULT_COUNTRY,
            )
        }.getOrNull()
    }

    private fun writeMeta(context: Context, code: String?, country: String) {
        val file = metaFile(context)
        if (code == null) {
            file.delete()
            return
        }
        file.writeText(JSONObject().put("code", code).put("country", country).toString())
    }

    private suspend fun fetchConfig(code: String, country: String): String =
        withContext(Dispatchers.IO) {
            val base = BuildConfig.KEY_SERVER_URL.trimEnd('/')
            check(base.isNotBlank()) { "адрес сервера ключей не задан в этой сборке" }
            val connection =
                (URL("$base/key/$code?country=$country").openConnection() as HttpURLConnection).apply {
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

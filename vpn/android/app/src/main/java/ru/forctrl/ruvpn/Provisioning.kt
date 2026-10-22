package ru.forctrl.ruvpn

import android.content.Context
import android.os.Build
import com.wireguard.config.Config
import com.wireguard.crypto.KeyPair
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

/**
 * Подключение без ключей. Список серверов зашит в приложение
 * (assets/servers.json — тот же файл vpn/shared/servers.json, что и в
 * Windows-версии). Ключ WireGuard создаётся на телефоне и никуда не
 * отправляется: на сервер (vpn/server/provision.py) уходит только его
 * публичная половина, в ответ приходит конфиг без PrivateKey — мы
 * вставляем свой ключ и подключаемся.
 */
object Provisioning {

    /** Порт службы регистрации на серверах (install_provision.sh). */
    private const val API_PORT = 8082

    /** Общий токен приложений — тот же, что в provision.py по умолчанию. */
    private const val APP_TOKEN = "ruvpn-app-1"

    private const val STORE_FILE = "devices.json"

    data class Server(val id: String, val name: String, val country: String, val host: String)

    private data class Registration(val privateKey: String, val publicKey: String, val config: String)

    fun servers(context: Context): List<Server> = runCatching {
        val json = context.assets.open("servers.json").use { it.readBytes().toString(Charsets.UTF_8) }
        val array = JSONArray(json)
        (0 until array.length()).map { index ->
            val item = array.getJSONObject(index)
            Server(
                id = item.getString("id"),
                name = item.getString("name"),
                country = item.optString("country"),
                host = item.getString("host"),
            )
        }
    }.getOrDefault(emptyList())

    private fun storeFile(context: Context) = File(context.filesDir, STORE_FILE)

    private fun readStore(context: Context): JSONObject =
        runCatching { JSONObject(storeFile(context).readText()) }.getOrDefault(JSONObject())

    private fun writeStore(context: Context, store: JSONObject) {
        storeFile(context).writeText(store.toString())
    }

    /** Выбранный сервер; если ничего не выбрано или его больше нет в списке — первый. */
    fun selected(context: Context): Server? {
        val all = servers(context)
        val id = readStore(context).optString("selected")
        return all.firstOrNull { it.id == id } ?: all.firstOrNull()
    }

    fun select(context: Context, id: String) {
        val store = readStore(context)
        store.put("selected", id)
        writeStore(context, store)
    }

    private fun registration(store: JSONObject, serverId: String): Registration? {
        val item = store.optJSONObject("registrations")?.optJSONObject(serverId) ?: return null
        val privateKey = item.optString("private_key")
        val publicKey = item.optString("public_key")
        if (privateKey.isBlank() || publicKey.isBlank()) return null
        return Registration(privateKey, publicKey, item.optString("config"))
    }

    private fun saveRegistration(context: Context, serverId: String, registration: Registration) {
        val store = readStore(context)
        val all = store.optJSONObject("registrations") ?: JSONObject()
        all.put(
            serverId,
            JSONObject()
                .put("private_key", registration.privateKey)
                .put("public_key", registration.publicKey)
                .put("config", registration.config),
        )
        store.put("registrations", all)
        writeStore(context, store)
    }

    /**
     * Конфиг для выбранного сервера. Ключ телефона создаётся один раз на
     * сервер; при каждом подключении регистрация повторяется (сервер отдаёт
     * актуальные настройки и заново добавляет устройство, если его
     * переустановили). Сервер регистрации недоступен, но настройки уже
     * есть — подключаемся с ними.
     */
    suspend fun configForSelected(context: Context): Config {
        val server = selected(context) ?: error("в приложении нет серверов")
        val store = readStore(context)
        var registration = registration(store, server.id) ?: KeyPair().let {
            Registration(it.privateKey.toBase64(), it.publicKey.toBase64(), "")
        }
        val fresh = runCatching { register(server, registration.publicKey) }
        fresh.onSuccess { registration = registration.copy(config = it) }
        if (fresh.isFailure && registration.config.isBlank()) throw fresh.exceptionOrNull()!!
        saveRegistration(context, server.id, registration)
        return parse(withPrivateKey(registration.config, registration.privateKey))
    }

    private suspend fun register(server: Server, publicKey: String): String =
        withContext(Dispatchers.IO) {
            val body = JSONObject()
                .put("token", APP_TOKEN)
                .put("public_key", publicKey)
                .put("device", "${Build.MANUFACTURER} ${Build.MODEL}".trim())
                .toString()
                .toByteArray(Charsets.UTF_8)
            val connection = runCatching {
                (URL("http://${server.host}:$API_PORT/register").openConnection() as HttpURLConnection).apply {
                    connectTimeout = 15_000
                    readTimeout = 15_000
                    requestMethod = "POST"
                    doOutput = true
                    setRequestProperty("Content-Type", "application/json")
                    outputStream.use { it.write(body) }
                }
            }.getOrElse { error("сервер «${server.name}» не отвечает") }
            try {
                val code = runCatching { connection.responseCode }
                    .getOrElse { error("сервер «${server.name}» не отвечает") }
                val stream = if (code in 200..299) connection.inputStream else connection.errorStream
                val text = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
                val json = runCatching { JSONObject(text) }
                    .getOrElse { error("сервер «${server.name}» ответил непонятно") }
                if (code !in 200..299) {
                    error("сервер «${server.name}»: ${json.optString("error", "ошибка сервера")}")
                }
                val config = json.optString("config")
                check(config.isNotBlank()) { "сервер «${server.name}» не прислал настройки" }
                config
            } finally {
                connection.disconnect()
            }
        }

    /** Вставляет свой приватный ключ сразу после строки [Interface]. */
    private fun withPrivateKey(config: String, privateKey: String): String {
        val out = StringBuilder()
        var inserted = false
        for (line in config.lines()) {
            out.append(line).append('\n')
            if (!inserted && line.trim().equals("[Interface]", ignoreCase = true)) {
                out.append("PrivateKey = ").append(privateKey).append('\n')
                inserted = true
            }
        }
        return out.toString()
    }

    private fun parse(text: String): Config = Config.parse(text.byteInputStream(Charsets.UTF_8))
}

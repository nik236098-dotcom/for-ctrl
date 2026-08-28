package ru.forctrl.ruvpn

import android.content.Context
import android.util.Base64
import com.wireguard.config.Config
import java.io.File

/**
 * Ключ доступа — одна строка вида `ruvpn://<base64>`, которую выдаёт
 * телеграм-бот. Внутри — обычные настройки туннеля, но пользователю об
 * этом знать незачем.
 *
 * Откуда берём ключ, по приоритету:
 *   1. тот, что человек вставил в приложении;
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

    /** Быстрая проверка «похоже на ключ» — чтобы подставлять из буфера обмена. */
    fun looksLikeKey(text: String?): Boolean {
        val value = text?.trim() ?: return false
        return value.startsWith(PREFIX, ignoreCase = true) || value.contains("[Interface]")
    }

    /**
     * Разбирает ключ. Понимает и короткую форму `ruvpn://…`, и обычный
     * текст настроек — на случай, если ключ пришёл из другого источника.
     */
    fun parse(text: String): Config {
        val body = decode(text)
        return Config.parse(body.byteInputStream(Charsets.UTF_8))
    }

    private fun decode(text: String): String {
        val value = text.trim()
        if (!value.startsWith(PREFIX, ignoreCase = true)) {
            return value
        }
        val payload = value.substring(PREFIX.length)
            .filterNot { it.isWhitespace() }
            .replace('-', '+')
            .replace('_', '/')
        val bytes = Base64.decode(payload, Base64.DEFAULT)
        return String(bytes, Charsets.UTF_8)
    }

    /** Адрес сервера — показываем его в карточке состояния. */
    fun endpointOf(config: Config): String =
        config.peers.firstOrNull()
            ?.endpoint
            ?.map { it.toString() }
            ?.orElse("—")
            ?: "—"
}

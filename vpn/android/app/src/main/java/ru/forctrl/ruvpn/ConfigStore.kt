package ru.forctrl.ruvpn

import android.content.Context
import android.net.Uri
import com.wireguard.config.Config
import java.io.File

/**
 * Где берётся конфиг туннеля, по приоритету:
 *   1. файл, импортированный пользователем (filesDir/wg.conf);
 *   2. assets/wg.conf — конфиг, «вшитый» в APK на этапе сборки.
 */
object ConfigStore {

    private const val FILE_NAME = "wg.conf"

    private fun importedFile(context: Context) = File(context.filesDir, FILE_NAME)

    fun rawText(context: Context): String? {
        val imported = importedFile(context)
        if (imported.exists()) {
            return imported.readText().takeIf { it.isNotBlank() }
        }
        return runCatching {
            context.assets.open(FILE_NAME).use { it.readBytes().toString(Charsets.UTF_8) }
        }.getOrNull()?.takeIf { it.isNotBlank() }
    }

    fun isImported(context: Context): Boolean = importedFile(context).exists()

    fun save(context: Context, text: String) {
        importedFile(context).writeText(text)
    }

    fun saveFrom(context: Context, uri: Uri) {
        val text = context.contentResolver.openInputStream(uri)?.use {
            it.readBytes().toString(Charsets.UTF_8)
        } ?: throw IllegalArgumentException("Не удалось прочитать файл")
        // Проверяем, что это действительно конфиг WireGuard, до сохранения.
        parse(text)
        save(context, text)
    }

    fun parse(text: String): Config =
        Config.parse(text.byteInputStream(Charsets.UTF_8))

    /** Endpoint первого пира — то, что показываем как «сервер». */
    fun endpointOf(config: Config): String =
        config.peers.firstOrNull()
            ?.endpoint
            ?.map { it.toString() }
            ?.orElse("—")
            ?: "—"
}

package ru.forctrl.ruvpn

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

/** Проверка «какой у меня сейчас внешний IP и в какой он стране». */
object IpChecker {

    private const val ENDPOINT = "https://ipwho.is/"

    data class Result(val ip: String, val country: String, val countryCode: String)

    suspend fun lookup(): Result = withContext(Dispatchers.IO) {
        val connection = (URL(ENDPOINT).openConnection() as HttpURLConnection).apply {
            connectTimeout = 10_000
            readTimeout = 10_000
            requestMethod = "GET"
        }
        try {
            val body = connection.inputStream.bufferedReader().use { it.readText() }
            val json = JSONObject(body)
            Result(
                ip = json.optString("ip", "—"),
                country = json.optString("country", "—"),
                countryCode = json.optString("country_code", ""),
            )
        } finally {
            connection.disconnect()
        }
    }
}

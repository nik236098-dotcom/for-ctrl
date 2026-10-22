package ru.forctrl.ruvpn

import android.content.Context
import com.wireguard.android.backend.Backend
import com.wireguard.android.backend.GoBackend
import com.wireguard.android.backend.Statistics
import com.wireguard.android.backend.Tunnel
import com.wireguard.config.Config
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext

/**
 * Единственная точка работы с туннелем: GoBackend живёт в процессе приложения,
 * состояние туннеля отдаётся наружу как StateFlow.
 */
object VpnManager {

    /** Имя интерфейса: <= 15 символов, иначе библиотека его отвергнет. */
    private const val TUNNEL_NAME = "ruvpn"

    private val _state = MutableStateFlow(Tunnel.State.DOWN)
    val state: StateFlow<Tunnel.State> = _state.asStateFlow()

    private val tunnel = object : Tunnel {
        override fun getName() = TUNNEL_NAME
        override fun onStateChange(newState: Tunnel.State) {
            _state.value = newState
        }
    }

    private var backend: Backend? = null

    fun init(context: Context) {
        if (backend == null) {
            backend = GoBackend(context.applicationContext)
            _state.value = backend!!.getState(tunnel)
        }
    }

    private fun requireBackend(): Backend =
        backend ?: error("VpnManager.init() не вызван")

    /**
     * Поднимает или опускает туннель. Вызов блокирующий (биндит VpnService и
     * ждёт его), поэтому всегда уходит в IO-диспетчер.
     */
    suspend fun setState(up: Boolean, config: Config?): Tunnel.State =
        withContext(Dispatchers.IO) {
            val target = if (up) Tunnel.State.UP else Tunnel.State.DOWN
            val result = requireBackend().setState(tunnel, target, config)
            _state.value = result
            result
        }

    suspend fun refreshState(): Tunnel.State = withContext(Dispatchers.IO) {
        val current = requireBackend().getState(tunnel)
        _state.value = current
        current
    }

    suspend fun statistics(): Statistics? = withContext(Dispatchers.IO) {
        runCatching { requireBackend().getStatistics(tunnel) }.getOrNull()
    }
}

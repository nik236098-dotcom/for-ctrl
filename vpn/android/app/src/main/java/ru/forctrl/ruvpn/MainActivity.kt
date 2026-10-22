package ru.forctrl.ruvpn

import android.net.VpnService
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.google.android.material.button.MaterialButton
import com.wireguard.android.backend.Tunnel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import ru.forctrl.ruvpn.databinding.ActivityMainBinding
import ru.forctrl.ruvpn.databinding.DialogCountryBinding
import ru.forctrl.ruvpn.databinding.ItemServerBinding
import java.util.Locale

/**
 * Ключей нет: сервер выбирается из встроенного списка, при первом
 * подключении телефон сам регистрируется на нём (см. [Provisioning]).
 */
class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var powerButton: PowerButtonAnimator

    /** Пока идёт подключение или отключение, статус показывает «Наводим связь…». */
    private var busy = false

    /** Разрешение системы на VPN — спрашивается один раз на установку. */
    private val vpnPermission = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        if (result.resultCode == RESULT_OK) {
            connect()
        } else {
            toast(getString(R.string.error_permission_denied))
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        VpnManager.init(this)
        powerButton = PowerButtonAnimator(binding.imagePowerOn)

        binding.buttonToggle.setOnClickListener { onToggleClicked() }
        binding.buttonServer.setOnClickListener { showServerDialog() }
        binding.ipRow.setOnClickListener { showServerDialog() }
        renderServerButton()

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                VpnManager.state.collectLatest { render(it) }
            }
        }
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.RESUMED) {
                while (true) {
                    updateTraffic()
                    delay(2_000)
                }
            }
        }
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.RESUMED) {
                while (true) {
                    // Только показывает IP. Неудачная проверка IP никогда не
                    // отключает VPN — сервис проверки IP тоже иногда падает.
                    delay(30_000)
                    if (!busy) refreshIp()
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        powerButton.onLifecycleResume()
        lifecycleScope.launch { VpnManager.refreshState() }
        checkIp()
    }

    override fun onPause() {
        super.onPause()
        powerButton.onLifecyclePause()
    }

    // --- действия -----------------------------------------------------------

    private fun onToggleClicked() {
        if (busy) return
        if (VpnManager.state.value == Tunnel.State.UP) {
            disconnect()
            return
        }
        val intent = VpnService.prepare(this)
        if (intent != null) {
            vpnPermission.launch(intent)
        } else {
            connect()
        }
    }

    private fun connect() {
        if (busy) return
        setBusy(true)
        lifecycleScope.launch {
            runCatching {
                val config = Provisioning.configForSelected(this@MainActivity)
                VpnManager.setState(up = true, config = config)
            }.onFailure { toast(getString(R.string.error_connect, it.messageOrClass())) }
            setBusy(false)
            checkIp()
        }
    }

    private fun disconnect() {
        setBusy(true)
        lifecycleScope.launch {
            runCatching { VpnManager.setState(up = false, config = null) }
                .onFailure { toast(getString(R.string.error_disconnect, it.messageOrClass())) }
            setBusy(false)
            checkIp()
        }
    }

    /** Окно выбора сервера: встроенный список, выбранный подсвечен. */
    private fun showServerDialog() {
        if (busy) return
        val dialogBinding = DialogCountryBinding.inflate(layoutInflater)
        val dialog = AlertDialog.Builder(this).setView(dialogBinding.root).create()
        dialog.window?.setBackgroundDrawableResource(android.R.color.transparent)

        val selectedId = Provisioning.selected(this)?.id
        for (server in Provisioning.servers(this)) {
            val row = ItemServerBinding.inflate(layoutInflater, dialogBinding.containerServers, false)
            val button: MaterialButton = row.buttonServer
            button.text = "${server.name} ${flagEmoji(server.country)}".trim()
            val on = server.id == selectedId
            button.setBackgroundColor(getColor(if (on) R.color.cyan else android.R.color.transparent))
            button.setTextColor(getColor(if (on) R.color.space_deep else R.color.cyan))
            button.setOnClickListener { chooseServer(server.id, dialog) }
            dialogBinding.containerServers.addView(row.root)
        }
        dialogBinding.textCountryCancel.setOnClickListener { dialog.dismiss() }

        dialog.show()
        dialog.window?.setLayout(
            (resources.displayMetrics.widthPixels * 0.88).toInt(),
            ViewGroup.LayoutParams.WRAP_CONTENT,
        )
    }

    private fun chooseServer(id: String, dialog: AlertDialog) {
        dialog.dismiss()
        if (busy || Provisioning.selected(this)?.id == id) return
        val wasUp = VpnManager.state.value == Tunnel.State.UP
        Provisioning.select(this, id)
        renderServerButton()
        toast(getString(R.string.server_changed))
        if (wasUp) lifecycleScope.launch { reconnect() }
    }

    /** После смены сервера при включённом VPN — переподключаемся на новый. */
    private suspend fun reconnect() {
        setBusy(true)
        val stopped = runCatching { VpnManager.setState(up = false, config = null) }
        stopped.onFailure { toast(getString(R.string.error_disconnect, it.messageOrClass())) }
        if (stopped.isSuccess) {
            runCatching {
                val config = Provisioning.configForSelected(this)
                VpnManager.setState(up = true, config = config)
            }.onFailure { toast(getString(R.string.error_connect, it.messageOrClass())) }
        }
        setBusy(false)
        checkIp()
    }

    /** Ваш ip показывается всегда, без отдельной кнопки — обновляется само. */
    private fun checkIp() {
        lifecycleScope.launch { refreshIp() }
    }

    private suspend fun refreshIp() {
        runCatching { IpChecker.lookup() }
            .onSuccess { result ->
                val flag = flagEmoji(result.countryCode)
                binding.textIp.text = getString(R.string.ip_line, "${result.ip} $flag".trim())
            }
            .onFailure {
                binding.textIp.text = getString(R.string.ip_unavailable)
            }
    }

    private fun flagEmoji(countryCode: String): String {
        if (countryCode.length != 2 || !countryCode.all { it.isLetter() }) return ""
        val base = 0x1F1E6 - 'A'.code
        return countryCode.uppercase(Locale.ROOT)
            .map { String(Character.toChars(base + it.code)) }
            .joinToString("")
    }

    // --- отрисовка ----------------------------------------------------------

    private fun renderServerButton() {
        val server = Provisioning.selected(this)
        binding.buttonServer.text = if (server != null) {
            getString(R.string.server_button, server.name)
        } else {
            getString(R.string.server_button_empty)
        }
    }

    private fun render(state: Tunnel.State) {
        val up = state == Tunnel.State.UP

        val visual = when {
            busy -> PowerVisualState.CONNECTING
            up -> PowerVisualState.UP
            else -> PowerVisualState.OFF
        }
        powerButton.apply(visual)

        if (!busy) {
            binding.textStatus.setText(
                if (up) R.string.status_connected else R.string.status_disconnected,
            )
            binding.textStatus.setTextColor(getColor(if (up) R.color.cyan else R.color.ink_soft))
        }
        binding.buttonToggle.contentDescription =
            getString(if (up) R.string.disconnect else R.string.connect)
        if (!up) {
            binding.textReceived.text = getString(R.string.traffic_placeholder)
            binding.textSent.text = getString(R.string.traffic_placeholder)
        }
    }

    private suspend fun updateTraffic() {
        if (VpnManager.state.value != Tunnel.State.UP) return
        val stats = VpnManager.statistics() ?: return
        binding.textReceived.text = formatBytes(stats.totalRx())
        binding.textSent.text = formatBytes(stats.totalTx())
        if (busy) return
        // Туннель поднят, но сервер ещё ни разу не ответил — трафика нет.
        val answered = stats.peers().any { key ->
            (stats.peer(key)?.latestHandshakeEpochMillis() ?: 0L) > 0L
        }
        binding.textStatus.setText(if (answered) R.string.status_connected else R.string.status_waiting)
    }

    private fun setBusy(busy: Boolean) {
        this.busy = busy
        binding.buttonToggle.isEnabled = !busy
        binding.progress.visibility = if (busy) View.VISIBLE else View.GONE
        if (busy) {
            binding.textStatus.setText(R.string.status_connecting)
            binding.textStatus.setTextColor(getColor(R.color.ink_soft))
            powerButton.apply(PowerVisualState.CONNECTING)
        } else {
            render(VpnManager.state.value)
        }
    }

    private fun toast(text: String) {
        Toast.makeText(this, text, Toast.LENGTH_LONG).show()
    }

    private fun Throwable.messageOrClass(): String =
        message?.takeIf { it.isNotBlank() } ?: this::class.java.simpleName

    private fun formatBytes(bytes: Long): String {
        val units = arrayOf("Б", "КБ", "МБ", "ГБ")
        var value = bytes.toDouble()
        var unit = 0
        while (value >= 1024 && unit < units.lastIndex) {
            value /= 1024
            unit++
        }
        return String.format(Locale.getDefault(), "%.1f %s", value, units[unit])
    }
}

package ru.forctrl.ruvpn

import android.content.ClipboardManager
import android.content.Context
import android.net.VpnService
import android.os.Bundle
import android.view.View
import android.widget.EditText
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.wireguard.android.backend.Tunnel
import com.wireguard.config.Config
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import ru.forctrl.ruvpn.databinding.ActivityMainBinding
import java.util.Locale

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding

    /** Пока идёт подключение, статус показывает «Строим, строим…». */
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

        binding.buttonToggle.setOnClickListener { onToggleClicked() }
        binding.buttonKey.setOnClickListener { showKeyDialog() }
        binding.buttonCheckIp.setOnClickListener { checkIp() }

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
    }

    override fun onResume() {
        super.onResume()
        lifecycleScope.launch { VpnManager.refreshState() }
    }

    // --- действия -----------------------------------------------------------

    private fun onToggleClicked() {
        if (VpnManager.state.value == Tunnel.State.UP) {
            disconnect()
            return
        }
        if (currentConfig() == null) {
            toast(getString(R.string.error_no_key))
            showKeyDialog()
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
        val config = currentConfig() ?: run {
            toast(getString(R.string.error_no_key))
            return
        }
        setBusy(true)
        lifecycleScope.launch {
            runCatching { VpnManager.setState(up = true, config = config) }
                .onFailure { toast(getString(R.string.error_connect, it.messageOrClass())) }
            setBusy(false)
        }
    }

    private fun disconnect() {
        setBusy(true)
        lifecycleScope.launch {
            runCatching { VpnManager.setState(up = false, config = null) }
                .onFailure { toast(getString(R.string.error_disconnect, it.messageOrClass())) }
            setBusy(false)
        }
    }

    /**
     * Единственный способ завести ключ: вставить строку из бота. Если она
     * уже в буфере обмена — подставляем сразу, чтобы осталось нажать «Готово».
     */
    private fun showKeyDialog() {
        val input = EditText(this).apply {
            hint = getString(R.string.key_dialog_hint)
            setPadding(48, 32, 48, 32)
            minLines = 3
            clipboardKey()?.let { setText(it) }
        }

        AlertDialog.Builder(this)
            .setTitle(R.string.key_dialog_title)
            .setMessage(R.string.key_dialog_help)
            .setView(input)
            .setPositiveButton(R.string.done) { _, _ -> saveKey(input.text.toString()) }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun saveKey(text: String) {
        runCatching {
            KeyStore.parse(text)
            KeyStore.save(this, text)
        }
            .onSuccess {
                toast(getString(R.string.key_saved))
                render(VpnManager.state.value)
            }
            .onFailure { toast(getString(R.string.error_bad_key)) }
    }

    private fun clipboardKey(): String? {
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
        val text = clipboard?.primaryClip
            ?.takeIf { it.itemCount > 0 }
            ?.getItemAt(0)
            ?.coerceToText(this)
            ?.toString()
        return text?.takeIf { KeyStore.looksLikeKey(it) }?.trim()
    }

    private fun checkIp() {
        binding.textIp.text = getString(R.string.ip_checking)
        lifecycleScope.launch {
            runCatching { IpChecker.lookup() }
                .onSuccess { result ->
                    val mark = if (result.countryCode.equals("RU", ignoreCase = true)) " ✅" else " ⚠️"
                    binding.textIp.text =
                        getString(R.string.ip_result, result.ip, result.country) + mark
                }
                .onFailure {
                    binding.textIp.text = getString(R.string.ip_error, it.messageOrClass())
                }
        }
    }

    // --- отрисовка ----------------------------------------------------------

    private fun currentConfig(): Config? {
        val text = KeyStore.rawText(this) ?: return null
        return runCatching { KeyStore.parse(text) }.getOrNull()
    }

    private fun render(state: Tunnel.State) {
        val up = state == Tunnel.State.UP
        if (!busy) {
            binding.textStatus.setText(
                if (up) R.string.status_connected else R.string.status_disconnected,
            )
            binding.textStatus.setTextColor(getColor(if (up) R.color.green else R.color.red))
        }
        binding.buttonToggle.setText(if (up) R.string.disconnect else R.string.connect)

        // Окно будки светится, а герой улыбается ровно тогда, когда связь есть.
        binding.imageBooth.setImageResource(if (up) R.drawable.booth_on else R.drawable.booth_off)
        binding.imageHero.setImageResource(if (up) R.drawable.hero_on else R.drawable.hero_off)

        val config = currentConfig()
        binding.textServer.text = when (config) {
            null -> getString(R.string.server_none)
            else -> getString(R.string.server_value, KeyStore.endpointOf(config))
        }
        binding.textKeyState.setText(
            when {
                config == null -> R.string.key_missing
                KeyStore.isOwn(this) -> R.string.key_own
                else -> R.string.key_bundled
            },
        )
        binding.buttonKey.setText(
            if (config == null) R.string.paste_key else R.string.replace_key,
        )
        if (!up) binding.textTraffic.text = getString(R.string.traffic_idle)
    }

    private suspend fun updateTraffic() {
        if (VpnManager.state.value != Tunnel.State.UP) return
        val stats = VpnManager.statistics() ?: return
        binding.textTraffic.text = getString(
            R.string.traffic_value,
            formatBytes(stats.totalRx()),
            formatBytes(stats.totalTx()),
        )
    }

    private fun setBusy(busy: Boolean) {
        this.busy = busy
        binding.buttonToggle.isEnabled = !busy
        binding.progress.visibility = if (busy) View.VISIBLE else View.GONE
        if (busy) {
            binding.textStatus.setText(R.string.status_connecting)
            binding.textStatus.setTextColor(getColor(R.color.ink_soft))
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

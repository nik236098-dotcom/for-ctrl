package ru.forctrl.ruvpn

import android.net.Uri
import android.net.VpnService
import android.os.Bundle
import android.widget.EditText
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

    /** Разрешение системы на VpnService — спрашивается один раз на установку. */
    private val vpnPermission = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        if (result.resultCode == RESULT_OK) {
            connect()
        } else {
            toast(getString(R.string.error_permission_denied))
        }
    }

    private val pickConfig = registerForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri: Uri? ->
        if (uri != null) importConfig(uri)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        VpnManager.init(this)

        binding.buttonToggle.setOnClickListener { onToggleClicked() }
        binding.buttonImportFile.setOnClickListener {
            pickConfig.launch(arrayOf("*/*"))
        }
        binding.buttonImportText.setOnClickListener { showPasteDialog() }
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
            toast(getString(R.string.error_no_config))
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
            toast(getString(R.string.error_no_config))
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

    private fun importConfig(uri: Uri) {
        runCatching { ConfigStore.saveFrom(this, uri) }
            .onSuccess {
                toast(getString(R.string.config_imported))
                render(VpnManager.state.value)
            }
            .onFailure { toast(getString(R.string.error_bad_config, it.messageOrClass())) }
    }

    private fun showPasteDialog() {
        val input = EditText(this).apply {
            hint = getString(R.string.paste_hint)
            setPadding(48, 32, 48, 32)
            minLines = 6
        }
        AlertDialog.Builder(this)
            .setTitle(R.string.paste_title)
            .setView(input)
            .setPositiveButton(R.string.save) { _, _ ->
                val text = input.text.toString()
                runCatching {
                    ConfigStore.parse(text)
                    ConfigStore.save(this, text)
                }
                    .onSuccess {
                        toast(getString(R.string.config_imported))
                        render(VpnManager.state.value)
                    }
                    .onFailure { toast(getString(R.string.error_bad_config, it.messageOrClass())) }
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun checkIp() {
        binding.textIp.text = getString(R.string.ip_checking)
        lifecycleScope.launch {
            runCatching { IpChecker.lookup() }
                .onSuccess { result ->
                    val ruMark = if (result.countryCode.equals("RU", ignoreCase = true)) " ✅" else " ⚠️"
                    binding.textIp.text =
                        getString(R.string.ip_result, result.ip, result.country) + ruMark
                }
                .onFailure {
                    binding.textIp.text = getString(R.string.ip_error, it.messageOrClass())
                }
        }
    }

    // --- отрисовка ----------------------------------------------------------

    private fun currentConfig(): Config? {
        val text = ConfigStore.rawText(this) ?: return null
        return runCatching { ConfigStore.parse(text) }.getOrNull()
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
        // Окно будки светится ровно тогда, когда туннель поднят.
        binding.imageBooth.setImageResource(if (up) R.drawable.booth_on else R.drawable.booth_off)

        val config = currentConfig()
        binding.textServer.text = when {
            config == null -> getString(R.string.server_none)
            else -> getString(R.string.server_value, ConfigStore.endpointOf(config))
        }
        binding.textConfigSource.text = getString(
            if (config == null) {
                R.string.config_source_missing
            } else if (ConfigStore.isImported(this)) {
                R.string.config_source_imported
            } else {
                R.string.config_source_bundled
            },
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
        binding.progress.visibility = if (busy) android.view.View.VISIBLE else android.view.View.GONE
        if (busy) {
            binding.textStatus.setText(R.string.status_connecting)
            binding.textStatus.setTextColor(getColor(R.color.ink_soft))
        } else {
            render(VpnManager.state.value)
        }
    }

    private fun toast(text: String) {
        android.widget.Toast.makeText(this, text, android.widget.Toast.LENGTH_LONG).show()
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

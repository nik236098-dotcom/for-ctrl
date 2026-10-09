package ru.forctrl.ruvpn

import android.content.ClipboardManager
import android.content.Context
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
import com.wireguard.android.backend.Tunnel
import com.wireguard.config.Config
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import ru.forctrl.ruvpn.databinding.ActivityMainBinding
import ru.forctrl.ruvpn.databinding.DialogCountryBinding
import ru.forctrl.ruvpn.databinding.DialogKeyBinding
import java.util.Locale

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var powerButton: PowerButtonAnimator

    /** Пока идёт подключение или отключение, статус показывает «Наводим связь…». */
    private var busy = false

    /** Тестовый ключ (см. [KeyStore.DEMO_KEY]): включён ли визуально — без реального тунеля. */
    private var demoUp = false

    /**
     * Служба тунеля и реальный пир на сервере — два независимых состояния:
     * /drop, истечение подписки и т.п. снимают пира на сервере, но
     * локальный WireGuard-тунель остаётся Tunnel.State.UP как ни в чём не
     * бывало. Раньше приложение в этом случае продолжало честно писать
     * "Впн включен", хотя реального интернета через тунель не было вообще.
     * Считаем несколько подряд неудачных проверок айпи (не одну — чтобы не
     * среагировать на случайный сетевой сбой) признаком мёртвого ключа.
     */
    private var ipFailureStreak = 0

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
        binding.buttonKey.setOnClickListener { showKeyDialog() }
        binding.ipRow.setOnClickListener { showCountryDialog() }

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
                    // 8, а не 15 секунд: connect()/reconnectWithSavedKey() уже делают
                    // первую проверку сразу при подключении — этот тик нужен только
                    // чтобы как можно быстрее набрать вторую (подтверждающую) неудачу
                    // подряд, не заставляя ждать почти минуту, если ключ мёртв сразу.
                    delay(8_000)
                    watchKeyStillWorks()
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
        if (KeyStore.isDemo(this)) {
            toggleDemo()
            return
        }
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

    /**
     * Тестовый ключ: только анимация кнопки, никакого реального тунеля —
     * ни VpnManager, ни разрешения на VPN, ни проверки IP.
     */
    private fun toggleDemo() {
        if (demoUp) {
            demoUp = false
            render(VpnManager.state.value)
            return
        }
        setBusy(true)
        lifecycleScope.launch {
            delay(900)
            demoUp = true
            setBusy(false)
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
            // watchKeyStillWorks() вместо простого checkIp(): если ключ мёртв уже в
            // момент подключения (не только "стал мёртвым посреди сессии"), раньше
            // это ловил только фоновый опрос, который тикает от запуска приложения,
            // а не от момента подключения — реальный случай мог остаться незамеченным
            // почти минуту. Теперь первая проверка идёт сразу же, тем же путём, что
            // и фоновая (она же обновляет "Ваш ip", как раньше делал checkIp()).
            watchKeyStillWorks()
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

    /**
     * Единственный способ завести ключ: вставить строку из бота. Если она
     * уже в буфере обмена — подставляем сразу; можно и явно нажать
     * «Вставить из буфера» — на случай, если автоопределение промахнулось.
     */
    private fun showKeyDialog() {
        val dialogBinding = DialogKeyBinding.inflate(layoutInflater)
        dialogBinding.inputKey.setText(clipboardText(onlyIfLooksLikeKey = true) ?: "")

        val dialog = AlertDialog.Builder(this)
            .setView(dialogBinding.root)
            .create()
        // Своя карточка со скруглёнными углами вместо системной рамки диалога.
        dialog.window?.setBackgroundDrawableResource(android.R.color.transparent)

        dialogBinding.buttonPasteClipboard.setOnClickListener {
            clipboardText(onlyIfLooksLikeKey = false)?.let { dialogBinding.inputKey.setText(it) }
        }
        dialogBinding.textCancel.setOnClickListener { dialog.dismiss() }
        dialogBinding.textDone.setOnClickListener {
            saveKey(dialogBinding.inputKey.text.toString())
            dialog.dismiss()
        }

        dialog.show()
        dialog.window?.setLayout(
            (resources.displayMetrics.widthPixels * 0.88).toInt(),
            ViewGroup.LayoutParams.WRAP_CONTENT,
        )
    }

    /**
     * Ключ, который вставляют — короткий код (8 символов): настоящие
     * настройки тунеля запрашиваются у сервера ключей один раз, здесь же
     * и сохраняются — дальше подключение работает офлайн.
     *
     * Тестовый ключ (см. [KeyStore.DEMO_KEY]) — особый случай: никуда не
     * стучимся, просто запоминаем, что включена демонстрация анимации.
     */
    private fun saveKey(text: String) {
        if (KeyStore.isDemoKey(text)) {
            KeyStore.saveDemo(this)
            toast(getString(R.string.key_saved))
            render(VpnManager.state.value)
            return
        }
        lifecycleScope.launch {
            runCatching {
                val resolved = KeyStore.resolve(text)
                KeyStore.parse(resolved.text) // проверка, что это и правда конфиг тунеля
                resolved
            }
                .onSuccess { resolved ->
                    KeyStore.save(this@MainActivity, resolved.text, resolved.code)
                    toast(getString(R.string.key_saved))
                    render(VpnManager.state.value)
                }
                .onFailure { toast(getString(R.string.error_bad_key, it.messageOrClass())) }
        }
    }

    /**
     * Клик по своему ip: если сохранённый ключ пришёл коротким кодом от
     * бота — можно сменить страну (тот же код переспрашивается у сервера
     * ключей с другим параметром страны, второй ключ вводить не нужно).
     * Для вставленного вручную конфига или старого base64-ключа кода нет —
     * тогда просто подсказываем, что нужен ключ от бота.
     */
    private fun showCountryDialog() {
        if (!KeyStore.hasSwitchableCode(this)) {
            toast(getString(R.string.error_no_switch))
            return
        }
        val dialogBinding = DialogCountryBinding.inflate(layoutInflater)
        val dialog = AlertDialog.Builder(this).setView(dialogBinding.root).create()
        dialog.window?.setBackgroundDrawableResource(android.R.color.transparent)

        fun highlight(selected: String) {
            for ((button, country) in listOf(
                dialogBinding.buttonCountryRu to "ru",
                dialogBinding.buttonCountryUs to "us",
            )) {
                val on = selected == country
                button.setBackgroundColor(getColor(if (on) R.color.cyan else android.R.color.transparent))
                button.setTextColor(getColor(if (on) R.color.space_deep else R.color.cyan))
            }
        }
        highlight(KeyStore.currentCountry(this))

        dialogBinding.buttonCountryRu.setOnClickListener { switchCountry("ru", dialog, ::highlight) }
        dialogBinding.buttonCountryUs.setOnClickListener { switchCountry("us", dialog, ::highlight) }
        dialogBinding.textCountryCancel.setOnClickListener { dialog.dismiss() }

        dialog.show()
        dialog.window?.setLayout(
            (resources.displayMetrics.widthPixels * 0.88).toInt(),
            ViewGroup.LayoutParams.WRAP_CONTENT,
        )
    }

    private fun switchCountry(country: String, dialog: AlertDialog, highlight: (String) -> Unit) {
        if (KeyStore.currentCountry(this) == country) {
            dialog.dismiss()
            return
        }
        val wasUp = VpnManager.state.value == Tunnel.State.UP
        lifecycleScope.launch {
            runCatching { KeyStore.switchCountry(this@MainActivity, country) }
                .onSuccess { changed ->
                    if (!changed) {
                        toast(getString(R.string.error_no_switch))
                        return@onSuccess
                    }
                    highlight(country)
                    toast(getString(R.string.server_changed))
                    dialog.dismiss()
                    if (wasUp) reconnectWithSavedKey()
                }
                .onFailure { toast(getString(R.string.error_switch_failed, it.messageOrClass())) }
        }
    }

    /** После смены страны, если тунель уже был поднят — переподключаемся на новый конфиг. */
    private suspend fun reconnectWithSavedKey() {
        setBusy(true)
        runCatching { VpnManager.setState(up = false, config = null) }
        val config = currentConfig()
        if (config != null) {
            runCatching { VpnManager.setState(up = true, config = config) }
                .onFailure { toast(getString(R.string.error_connect, it.messageOrClass())) }
        }
        setBusy(false)
        // Та же логика, что и в connect(): проверяем сразу же тем же путём, что и
        // фоновый опрос, а не ждём его следующего тика.
        watchKeyStillWorks()
    }

    /** [onlyIfLooksLikeKey] — для автоподстановки при открытии; false — для явной кнопки «Вставить». */
    private fun clipboardText(onlyIfLooksLikeKey: Boolean): String? {
        val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
        val text = clipboard?.primaryClip
            ?.takeIf { it.itemCount > 0 }
            ?.getItemAt(0)
            ?.coerceToText(this)
            ?.toString()
            ?.trim()
        return if (onlyIfLooksLikeKey) text?.takeIf { KeyStore.looksLikeKey(it) } else text
    }

    /** Ваш ip показывается всегда, без отдельной кнопки — обновляется само. */
    private fun checkIp() {
        lifecycleScope.launch { refreshIp() }
    }

    /** Возвращает true, если айпи реально удалось узнать (тунель что-то пропускает). */
    private suspend fun refreshIp(): Boolean =
        runCatching { IpChecker.lookup() }
            .onSuccess { result ->
                val flag = flagEmoji(result.countryCode)
                binding.textIp.text = getString(R.string.ip_line, "${result.ip} $flag".trim())
            }
            .onFailure {
                binding.textIp.text = getString(R.string.ip_unavailable)
            }
            .isSuccess

    /**
     * Пока тунель "включен", раз в 8 секунд тихо проверяем айпи (плюс сразу
     * же при подключении — см. connect()/reconnectWithSavedKey()). Два
     * подряд неудачных обращения — тунель локально поднят, а ключ на
     * сервере больше не работает (см. комментарий у [ipFailureStreak]).
     * В этом случае сами отключаемся и честно говорим об этом, вместо
     * того чтобы бесконечно висеть "включенным" без реального интернета.
     */
    private suspend fun watchKeyStillWorks() {
        if (busy || KeyStore.isDemo(this)) return
        if (VpnManager.state.value != Tunnel.State.UP) {
            ipFailureStreak = 0
            return
        }
        if (refreshIp()) {
            ipFailureStreak = 0
            return
        }
        ipFailureStreak++
        if (ipFailureStreak >= 2) {
            ipFailureStreak = 0
            handleDeadKey()
        }
    }

    private suspend fun handleDeadKey() {
        toast(getString(R.string.error_key_dead))
        runCatching { VpnManager.setState(up = false, config = null) }
        render(VpnManager.state.value)
    }

    private fun flagEmoji(countryCode: String): String {
        if (countryCode.length != 2 || !countryCode.all { it.isLetter() }) return ""
        val base = 0x1F1E6 - 'A'.code
        return countryCode.uppercase(Locale.ROOT)
            .map { String(Character.toChars(base + it.code)) }
            .joinToString("")
    }

    // --- отрисовка ----------------------------------------------------------

    private fun currentConfig(): Config? {
        val text = KeyStore.rawText(this) ?: return null
        return runCatching { KeyStore.parse(text) }.getOrNull()
    }

    private fun render(state: Tunnel.State) {
        val up = if (KeyStore.isDemo(this)) demoUp else state == Tunnel.State.UP

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

        val hasAnyKey = KeyStore.rawText(this) != null
        binding.buttonKey.setText(
            if (hasAnyKey) R.string.replace_key else R.string.paste_key,
        )
        binding.textChangeServerHint.visibility =
            if (KeyStore.hasSwitchableCode(this)) View.VISIBLE else View.GONE
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

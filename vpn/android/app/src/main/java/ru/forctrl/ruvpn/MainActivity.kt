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
import com.google.android.material.button.MaterialButton
import com.wireguard.android.backend.Tunnel
import com.wireguard.config.Config
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import ru.forctrl.ruvpn.databinding.ActivityMainBinding
import ru.forctrl.ruvpn.databinding.DialogCountryBinding
import ru.forctrl.ruvpn.databinding.DialogKeyBinding
import ru.forctrl.ruvpn.databinding.ItemServerBinding
import java.util.Locale

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private lateinit var powerButton: PowerButtonAnimator

    /** Пока идёт подключение или отключение, статус показывает «Наводим связь…». */
    private var busy = false

    /** Тестовый ключ (см. [KeyStore.DEMO_KEY]): включён ли визуально — без реального тунеля. */
    private var demoUp = false

    /**
     * Сколько раз подряд собственный сервер ключей явно ответил "revoked".
     * Только это — повод отключить туннель самим. Сбой сервиса проверки IP
     * о ключе ничего не говорит (раньше по нему туннель отключался зря,
     * как и в Windows-версии до 0.1.1).
     */
    private var revokedStreak = 0

    /** Растёт при каждом подключении/переключении — старые проверки отбрасываются. */
    private var connectionGeneration = 0

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
                    // IP и статус ключа проверяются раз в 30 секунд, как в Windows.
                    delay(30_000)
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
    private fun showKeyDialog(forNewServer: Boolean = false) {
        val dialogBinding = DialogKeyBinding.inflate(layoutInflater)
        if (forNewServer) {
            dialogBinding.textKeyTitle.setText(R.string.add_server_title)
            dialogBinding.textKeyHelp.setText(R.string.add_server_help)
        }
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
            val text = dialogBinding.inputKey.text.toString()
            if (forNewServer) addServer(text) else saveKey(text)
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
     * Свой сервер рядом с основным ключом (например, запасной). Основной
     * ключ не меняется; новый сервер сразу выбирается, и если VPN был
     * включён — переподключаемся через него.
     */
    private fun addServer(text: String) {
        if (text.isBlank()) return
        val wasUp = VpnManager.state.value == Tunnel.State.UP
        lifecycleScope.launch {
            runCatching {
                val resolved = KeyStore.resolve(text)
                KeyStore.addServer(this@MainActivity, resolved.text)
            }
                .onSuccess { server ->
                    toast(getString(R.string.server_added, server.name))
                    if (wasUp) reconnectWithSavedKey() else render(VpnManager.state.value)
                }
                .onFailure { toast(getString(R.string.error_bad_key, it.messageOrClass())) }
        }
    }

    /**
     * Клик по своему ip — окно «Сервер»: основной ключ (Россия/США, если он
     * от бота, иначе одна кнопка с адресом сервера), свои серверы и
     * «+ Добавить сервер». Выбранный подсвечен.
     */
    private fun showCountryDialog() {
        if (busy) return
        val dialogBinding = DialogCountryBinding.inflate(layoutInflater)
        val dialog = AlertDialog.Builder(this).setView(dialogBinding.root).create()
        dialog.window?.setBackgroundDrawableResource(android.R.color.transparent)

        fun paint(button: MaterialButton, on: Boolean) {
            button.setBackgroundColor(getColor(if (on) R.color.cyan else android.R.color.transparent))
            button.setTextColor(getColor(if (on) R.color.space_deep else R.color.cyan))
        }

        fun refresh() {
            val servers = KeyStore.servers(this)
            val switchable = KeyStore.hasSwitchableCode(this)
            val mainSelected = servers.selected == null
            val country = KeyStore.currentCountry(this)
            val countryVisibility = if (switchable) View.VISIBLE else View.GONE
            dialogBinding.buttonCountryRu.visibility = countryVisibility
            dialogBinding.buttonCountryUs.visibility = countryVisibility
            paint(dialogBinding.buttonCountryRu, mainSelected && country == "ru")
            paint(dialogBinding.buttonCountryUs, mainSelected && country == "us")
            dialogBinding.buttonMainServer.visibility =
                if (KeyStore.hasMainKey(this) && !switchable) View.VISIBLE else View.GONE
            dialogBinding.buttonMainServer.text =
                KeyStore.mainName(this) ?: getString(R.string.main_key)
            paint(dialogBinding.buttonMainServer, mainSelected)

            dialogBinding.containerServers.removeAllViews()
            for (server in servers.list) {
                val row = ItemServerBinding.inflate(layoutInflater, dialogBinding.containerServers, false)
                row.buttonServer.text = server.name
                paint(row.buttonServer, server.id == servers.selected)
                row.buttonServer.setOnClickListener { chooseServer(server.id, dialog) }
                row.buttonRemove.setOnClickListener { confirmRemoveServer(server) { refresh() } }
                dialogBinding.containerServers.addView(row.root)
            }
        }
        refresh()

        dialogBinding.buttonCountryRu.setOnClickListener { switchCountry("ru", dialog) }
        dialogBinding.buttonCountryUs.setOnClickListener { switchCountry("us", dialog) }
        dialogBinding.buttonMainServer.setOnClickListener { chooseServer(null, dialog) }
        dialogBinding.buttonAddServer.setOnClickListener {
            dialog.dismiss()
            showKeyDialog(forNewServer = true)
        }
        dialogBinding.textCountryCancel.setOnClickListener { dialog.dismiss() }

        dialog.show()
        dialog.window?.setLayout(
            (resources.displayMetrics.widthPixels * 0.88).toInt(),
            ViewGroup.LayoutParams.WRAP_CONTENT,
        )
    }

    /** Свой сервер или основной ключ (id = null). */
    private fun chooseServer(id: String?, dialog: AlertDialog) {
        if (busy) return
        if (KeyStore.servers(this).selected == id) {
            dialog.dismiss()
            return
        }
        val wasUp = VpnManager.state.value == Tunnel.State.UP
        KeyStore.selectServer(this, id)
        toast(getString(R.string.server_changed))
        dialog.dismiss()
        if (wasUp) {
            lifecycleScope.launch { reconnectWithSavedKey() }
        } else {
            render(VpnManager.state.value)
        }
    }

    private fun confirmRemoveServer(server: KeyStore.Server, onRemoved: () -> Unit) {
        if (busy) return
        AlertDialog.Builder(this)
            .setMessage(getString(R.string.remove_server_confirm, server.name))
            .setPositiveButton(R.string.remove) { _, _ ->
                val wasSelected = KeyStore.removeServer(this, server.id)
                toast(getString(R.string.server_removed))
                onRemoved()
                // Удалили сервер, через который были подключены, — переходим на основной ключ.
                if (wasSelected && VpnManager.state.value == Tunnel.State.UP) {
                    lifecycleScope.launch { reconnectWithSavedKey() }
                } else {
                    render(VpnManager.state.value)
                }
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun switchCountry(country: String, dialog: AlertDialog) {
        if (busy) return
        if (KeyStore.servers(this).selected == null && KeyStore.currentCountry(this) == country) {
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
                    toast(getString(R.string.server_changed))
                    dialog.dismiss()
                    if (wasUp) reconnectWithSavedKey() else render(VpnManager.state.value)
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
     * Пока тунель включён, раз в 30 секунд (и сразу после подключения)
     * обновляем «Ваш ip» и спрашиваем у собственного сервера ключей статус
     * ключа. Отключаемся сами только после двух подряд явных "revoked".
     * Неудачная проверка IP, таймаут, старый сервер без /status — это
     * «неизвестно», туннель остаётся включённым. Свои серверы и вставленные
     * вручную конфиги боту не подчиняются и не проверяются вовсе.
     */
    private suspend fun watchKeyStillWorks() {
        if (busy || KeyStore.isDemo(this)) return
        val generation = connectionGeneration
        if (VpnManager.state.value != Tunnel.State.UP) {
            revokedStreak = 0
            return
        }
        refreshIp()
        val status = KeyStore.keyStatus(this)
        // Пока шла проверка, могли переподключиться или сменить сервер.
        if (busy || generation != connectionGeneration) return
        if (status != "revoked") {
            revokedStreak = 0
            return
        }
        revokedStreak++
        if (revokedStreak >= 2) {
            revokedStreak = 0
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
        val text = KeyStore.activeText(this) ?: return null
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
        // Окно «Сервер» доступно всегда: в нём же добавляются свои серверы.
        binding.textChangeServerHint.visibility = View.VISIBLE
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
        if (busy || KeyStore.isDemo(this)) return
        // Туннель поднят, но сервер ещё ни разу не ответил — трафика нет.
        val answered = stats.peers().any { key ->
            (stats.peer(key)?.latestHandshakeEpochMillis() ?: 0L) > 0L
        }
        binding.textStatus.setText(if (answered) R.string.status_connected else R.string.status_waiting)
    }

    private fun setBusy(busy: Boolean) {
        if (busy) {
            // Ответ старой проверки не должен отключить новое соединение.
            connectionGeneration++
            revokedStreak = 0
        }
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

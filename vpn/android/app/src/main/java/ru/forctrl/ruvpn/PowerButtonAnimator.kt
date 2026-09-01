package ru.forctrl.ruvpn

import android.animation.Animator
import android.animation.ObjectAnimator
import android.animation.ValueAnimator
import android.os.Build
import android.widget.ImageView

enum class PowerVisualState { OFF, CONNECTING, UP }

/**
 * Кнопка — две картинки друг над другом: выключенная (всегда на месте) и
 * включённая (светящаяся), которая проявляется поверх неё кроссфейдом.
 * Отдельного кольца больше нет — оно уже запечено в обеих картинках.
 */
class PowerButtonAnimator(private val glow: ImageView) {

    private var glowAnimator: Animator? = null

    fun apply(state: PowerVisualState) {
        glowAnimator?.cancel()
        val reduceMotion = !animationsEnabled()

        glowAnimator = when (state) {
            PowerVisualState.OFF -> fadeTo(glow, 0f, reduceMotion)
            PowerVisualState.UP -> fadeTo(glow, 1f, reduceMotion)
            PowerVisualState.CONNECTING -> breathing(glow, reduceMotion)
        }
    }

    /** Ничего не крутится — оставлены для совместимости с вызовами из активности. */
    fun onLifecyclePause() {}

    fun onLifecycleResume() {}

    private fun fadeTo(view: ImageView, target: Float, reduceMotion: Boolean): Animator? {
        if (reduceMotion) {
            view.alpha = target
            return null
        }
        return ObjectAnimator.ofFloat(view, "alpha", view.alpha, target).apply {
            duration = FADE_MS
            start()
        }
    }

    /** Пока идёт подключение/отключение — ровный пульс, «идёт работа». */
    private fun breathing(view: ImageView, reduceMotion: Boolean): Animator? {
        if (reduceMotion) {
            view.alpha = 0.5f
            return null
        }
        return ValueAnimator.ofFloat(0.25f, 0.65f).apply {
            duration = BREATH_MS
            repeatMode = ValueAnimator.REVERSE
            repeatCount = ValueAnimator.INFINITE
            addUpdateListener { view.alpha = it.animatedValue as Float }
            start()
        }
    }

    private fun animationsEnabled(): Boolean =
        // areAnimatorsEnabled() появился в API 26; minSdk у нас 24, поэтому
        // сперва проверяем версию — вызов недоступного метода на старом
        // устройстве это NoSuchMethodError при верификации класса, а не то,
        // что стоит полагаться поймать через try/catch.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            ValueAnimator.areAnimatorsEnabled()
        } else {
            true
        }

    companion object {
        private const val FADE_MS = 300L
        private const val BREATH_MS = 900L
    }
}

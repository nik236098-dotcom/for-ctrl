package ru.forctrl.ruvpn

import android.animation.Animator
import android.animation.ObjectAnimator
import android.animation.ValueAnimator
import android.os.Build
import android.view.animation.LinearInterpolator
import android.widget.ImageView

enum class PowerVisualState { OFF, CONNECTING, UP }

/**
 * Управляет двумя слоями над корпусом кнопки: статичным свечением
 * (тускнеет/разгорается по состоянию, во время подключения — «дышит») и
 * вращающимся ободком-нимбом позади кнопки.
 *
 * Ободок не пересоздаётся при каждом переключении — он ставится на паузу и
 * снимается с неё (`pause()`/`resume()`), чтобы не дёргаться на исходный
 * угол при каждом отключении/включении.
 */
class PowerButtonAnimator(
    private val glow: ImageView,
    private val ring: ImageView,
) {
    private var glowAnimator: Animator? = null
    private var ringRotator: ObjectAnimator? = null
    private var ringShouldSpin = false

    fun apply(state: PowerVisualState) {
        glowAnimator?.cancel()
        val reduceMotion = !animationsEnabled()

        glowAnimator = when (state) {
            PowerVisualState.OFF -> fadeTo(glow, 0f, reduceMotion)
            PowerVisualState.UP -> fadeTo(glow, 1f, reduceMotion)
            PowerVisualState.CONNECTING -> breathing(glow, reduceMotion)
        }

        val ringTarget = if (state == PowerVisualState.OFF) 0f else 0.9f
        ring.animate().cancel()
        if (reduceMotion) {
            ring.alpha = ringTarget
        } else {
            ring.animate().alpha(ringTarget).setDuration(FADE_MS).start()
        }

        ringShouldSpin = state != PowerVisualState.OFF && !reduceMotion
        if (ringShouldSpin) startOrResumeSpin() else pauseSpin()
    }

    /** Вызывать из onPause/onResume активности — вращение не крутится, пока экран не виден. */
    fun onLifecyclePause() {
        ringRotator?.pause()
    }

    fun onLifecycleResume() {
        if (ringShouldSpin) startOrResumeSpin()
    }

    private fun startOrResumeSpin() {
        val existing = ringRotator
        if (existing != null) {
            if (existing.isPaused) existing.resume() else if (!existing.isRunning) existing.start()
            return
        }
        ringRotator = ObjectAnimator.ofFloat(ring, "rotation", ring.rotation, ring.rotation + 360f).apply {
            duration = ROTATION_MS
            interpolator = LinearInterpolator()
            repeatCount = ValueAnimator.INFINITE
            start()
        }
    }

    private fun pauseSpin() {
        ringRotator?.pause()
    }

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
        private const val ROTATION_MS = 5_000L
    }
}

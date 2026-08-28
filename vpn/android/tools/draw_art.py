#!/usr/bin/env python3
"""Генератор иллюстраций: герой, телефонная будка и иконка приложения.

Рисовать такое руками в XML невозможно, поэтому фигуры собираются здесь,
а состояния «связи нет» и «связь есть» отличаются только выражением лица,
позой лап и апельсином. Запуск:

    python3 vpn/android/tools/draw_art.py

Предпросмотр — vd2svg (см. README в этой папке).
"""
from pathlib import Path

RES = Path(__file__).resolve().parent.parent / "app/src/main/res/drawable"
K = 0.5523  # магическая константа для окружности из четырёх кубик-безье


def ellipse(cx: float, cy: float, rx: float, ry: float) -> str:
    ox, oy = rx * K, ry * K
    return (
        f"M{cx},{cy - ry} "
        f"C{cx + ox},{cy - ry} {cx + rx},{cy - oy} {cx + rx},{cy} "
        f"C{cx + rx},{cy + oy} {cx + ox},{cy + ry} {cx},{cy + ry} "
        f"C{cx - ox},{cy + ry} {cx - rx},{cy + oy} {cx - rx},{cy} "
        f"C{cx - rx},{cy - oy} {cx - ox},{cy - ry} {cx},{cy - ry} Z"
    )


def path(d: str, fill: str = None, gradient: str = None, stroke: str = None,
         width: float = 0, cap: str = "round", join: str = "round",
         fill_alpha: float = None, stroke_alpha: float = None) -> str:
    attrs = [f'android:pathData="{d}"']
    if stroke:
        attrs += [
            f'android:strokeColor="{stroke}"',
            f'android:strokeWidth="{width}"',
            f'android:strokeLineCap="{cap}"',
            f'android:strokeLineJoin="{join}"',
        ]
        if stroke_alpha is not None:
            attrs.append(f'android:strokeAlpha="{stroke_alpha}"')
    if fill and not gradient:
        attrs.append(f'android:fillColor="{fill}"')
    if fill_alpha is not None:
        attrs.append(f'android:fillAlpha="{fill_alpha}"')
    if not gradient:
        if not fill:
            attrs.append('android:fillColor="#00000000"')
        return "    <path\n        " + "\n        ".join(attrs) + " />\n"
    return (
        "    <path\n        " + "\n        ".join(attrs) + ">\n"
        '        <aapt:attr name="android:fillColor">\n'
        + gradient
        + "        </aapt:attr>\n    </path>\n"
    )


def soft_light(cx: float, cy: float, rx: float, ry: float,
               color: str = "#FFF3D8", alpha: str = "59") -> str:
    """Блик, растворяющийся в прозрачность: без видимого края овала."""
    return path(
        ellipse(cx, cy, rx, ry),
        gradient=radial(cx, cy, max(rx, ry),
                        [(0.0, f"#{alpha}{color[1:]}"), (1.0, f"#00{color[1:]}")]),
    )


def radial(cx: float, cy: float, r: float, stops: list[tuple[float, str]]) -> str:
    items = "".join(
        f'                <item android:offset="{offset}" android:color="{color}" />\n'
        for offset, color in stops
    )
    return (
        f'            <gradient\n'
        f'                android:type="radial"\n'
        f'                android:centerX="{cx}"\n'
        f'                android:centerY="{cy}"\n'
        f'                android:gradientRadius="{r}">\n'
        f"{items}"
        f"            </gradient>\n"
    )


def linear(x1: float, y1: float, x2: float, y2: float,
           stops: list[tuple[float, str]]) -> str:
    items = "".join(
        f'                <item android:offset="{offset}" android:color="{color}" />\n'
        for offset, color in stops
    )
    return (
        f'            <gradient\n'
        f'                android:startX="{x1}"\n'
        f'                android:startY="{y1}"\n'
        f'                android:endX="{x2}"\n'
        f'                android:endY="{y2}">\n'
        f"{items}"
        f"            </gradient>\n"
    )


# --- палитра ----------------------------------------------------------------
SHADOW = [(0.0, "#59000000"), (0.7, "#26000000"), (1.0, "#00000000")]
GROUND = ellipse(100, 248, 66, 13)

INK = "@color/ink"

# --- крокодил Гена ----------------------------------------------------------

GREEN_SKIN = [(0.0, "#FF8FC47A"), (0.5, "#FF5E9A52"), (1.0, "#FF2F5A31")]
GREEN_JAW = [(0.0, "#FFE2ECB4"), (0.6, "#FFC3D791"), (1.0, "#FF93AE68")]
COAT = [(0.0, "#FFC94A38"), (0.55, "#FFA8342A"), (1.0, "#FF6E1F16")]
COAT_DARK = [(0.0, "#FF9C2E24"), (1.0, "#FF5E1A12")]
SHIRT = [(0.0, "#FFFFFBF0"), (0.7, "#FFF2E6CC"), (1.0, "#FFD8C6A4")]
HAT = [(0.0, "#FF4A423A"), (0.5, "#FF2B2622"), (1.0, "#FF171310")]
EYE_BALL = [(0.0, "#FFFFFFFF"), (0.75, "#FFF3EAD8"), (1.0, "#FFD9CBB0")]

# Лоб плавно перетекает в морду — одним контуром, без ступеньки на стыке.
GENA_HEAD = (
    "M110,46 C140,44 168,66 168,96 C168,122 148,140 120,141 "
    "C104,142 92,140 84,137 "
    "C68,136 46,132 34,126 "
    "C24,120 22,110 30,104 "
    "C44,96 70,86 90,76 "
    "C98,66 100,52 110,46 Z"
)
GENA_JAW = (
    "M28,112 C44,120 72,124 94,120 C96,128 92,136 84,137 "
    "C68,136 46,132 34,126 C26,122 24,116 28,112 Z"
)
GENA_NECK = "M90,126 L120,126 L124,158 L88,158 Z"
GENA_COAT = (
    "M88,138 C74,140 62,150 56,168 C50,186 50,208 54,222 "
    "C60,234 82,238 100,238 C118,238 140,234 146,222 "
    "C150,208 150,186 144,168 C138,150 126,140 112,138 Z"
)
GENA_SHIRT = (
    "M90,134 L112,134 L115,176 C115,188 108,195 101,197 "
    "C94,195 87,188 87,176 Z"
)
COLLAR_L = "M90,132 L106,150 L82,155 Z"
COLLAR_R = "M112,132 L98,150 L121,154 Z"
LAPEL_L = "M88,138 C92,158 96,180 98,202 L86,202 C82,178 78,156 74,144 Z"
LAPEL_R = "M112,138 C108,158 104,180 102,202 L114,202 C118,178 122,156 126,144 Z"
HAT_BRIM = ellipse(100, 38, 40, 9.5)
HAT_CROWN = "M76,38 C76,20 85,11 100,11 C115,11 124,20 124,36 C112,42 88,43 76,38 Z"
HAT_BAND = "M77,32 C90,38 111,37 123,31 C123,34 124,35 124,36 C112,42 88,43 76,38 Z"
FOOT_GENA_L = ellipse(82, 242, 21, 12)
FOOT_GENA_R = ellipse(120, 242, 21, 12)
PAW_L = ellipse(52, 214, 14, 13)
PAW_R = ellipse(148, 214, 14, 13)
CUFF_L = "M44,200 C52,196 64,198 68,206 C60,210 50,210 44,206 Z"
CUFF_R = "M156,200 C148,196 136,198 132,206 C140,210 150,210 156,206 Z"
NOSTRIL = ellipse(38, 104, 4, 3)

MOUTH_FLAT = "M27,111 C46,119 72,123 94,120"
MOUTH_SMILE = "M27,109 C46,121 76,126 100,114"


def gena_eye(cx: float, cy: float, look_x: float, look_y: float) -> str:
    """Глаз-шар поверх головы: белок, серо-голубая радужка, зрачок, блики."""
    out = path(ellipse(cx, cy, 20, 20),
               gradient=radial(cx - 6, cy - 8, 26, EYE_BALL), stroke=INK, width=3)
    out += path(ellipse(cx + look_x, cy + look_y, 11, 11), fill="#FF6E8AA6")
    out += path(ellipse(cx + look_x, cy + look_y, 6.5, 6.5), fill="#FF191410")
    out += path(ellipse(cx + look_x - 4.5, cy + look_y - 5, 4.2, 4.6), fill="#FFFFFFFF")
    out += path(ellipse(cx + look_x + 5, cy + look_y + 6, 2, 2), fill="#FFFFFFFF",
                fill_alpha=0.7)
    return out


def gena(connected: bool) -> str:
    look_y = -1 if connected else 3
    parts = [
        path(GROUND, gradient=radial(100, 248, 66, SHADOW)),
        # пальто и то, что под ним
        path(FOOT_GENA_L, gradient=radial(76, 240, 26, GREEN_SKIN), stroke=INK, width=3),
        path(FOOT_GENA_R, gradient=radial(110, 240, 26, GREEN_SKIN), stroke=INK, width=3),
        path(PAW_L, gradient=radial(46, 208, 22, GREEN_SKIN), stroke=INK, width=3),
        path(PAW_R, gradient=radial(154, 208, 22, GREEN_SKIN), stroke=INK, width=3),
        path(CUFF_L, gradient=linear(44, 198, 68, 210, COAT_DARK), stroke=INK, width=2.5),
        path(CUFF_R, gradient=linear(156, 198, 132, 210, COAT_DARK), stroke=INK, width=2.5),
        path(GENA_COAT, gradient=radial(78, 156, 120, COAT), stroke=INK, width=3.5),
        path(GENA_SHIRT, gradient=linear(86, 136, 114, 199, SHIRT), stroke=INK, width=2.5),
        path(LAPEL_L, gradient=linear(74, 140, 96, 197, COAT_DARK)),
        path(LAPEL_R, gradient=linear(126, 140, 104, 197, COAT_DARK)),
        path(ellipse(101, 170, 3.8, 3.8), fill="@color/mustard", stroke=INK, width=1.5),
        path(ellipse(101, 188, 3.8, 3.8), fill="@color/mustard", stroke=INK, width=1.5),
        # шея под воротником, затем голова
        path(GENA_NECK, gradient=linear(90, 128, 122, 156, GREEN_SKIN), stroke=INK, width=2.5),
        path(COLLAR_L, gradient=linear(86, 135, 105, 152, SHIRT), stroke=INK, width=2),
        path(COLLAR_R, gradient=linear(117, 135, 99, 152, SHIRT), stroke=INK, width=2),
        path(GENA_HEAD, gradient=radial(84, 66, 130, GREEN_SKIN), stroke=INK, width=3.5),
        path(GENA_JAW, gradient=linear(34, 108, 94, 134, GREEN_JAW), stroke=INK, width=2.5),
        soft_light(96, 74, 30, 20),
        path(NOSTRIL, fill="#FF23331F"),
        path(MOUTH_SMILE if connected else MOUTH_FLAT, stroke=INK, width=3),
        # глаза поверх головы: они сидят шарами на макушке
        gena_eye(101, 48, -3, look_y),
        gena_eye(139, 54, -3, look_y),
        # шляпа
        '    <group\n        android:pivotX="98"\n        android:pivotY="34"\n'
        '        android:rotation="-11">\n',
        path(HAT_BRIM, gradient=linear(54, 34, 142, 48, HAT), stroke=INK, width=3),
        path(HAT_CROWN, gradient=linear(72, 9, 124, 44, HAT), stroke=INK, width=3),
        path(HAT_BAND, fill="#FF0F0C0A", fill_alpha=0.55),
        "    </group>\n",
    ]

    state = "связь есть" if connected else "связи нет"
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f"<!-- Крокодил Гена, {state}. Файл сгенерирован tools/draw_art.py. -->\n"
        '<vector xmlns:android="http://schemas.android.com/apk/res/android"\n'
        '    xmlns:aapt="http://schemas.android.com/aapt"\n'
        '    android:width="160dp"\n'
        '    android:height="208dp"\n'
        '    android:viewportWidth="200"\n'
        '    android:viewportHeight="260">\n\n'
        + "".join(parts)
        + "</vector>\n"
    )


# --- телефонная будка -------------------------------------------------------


GREEN = [(0.0, "#FF6E9E4E"), (0.55, "#FF4F7A3A"), (1.0, "#FF32522A")]
ROOF = [(0.0, "#FFD35C48"), (0.6, "#FFB23A2E"), (1.0, "#FF7E2418")]
GLASS_OFF = [(0.0, "#FFA9B79C"), (0.6, "#FF8C9A82"), (1.0, "#FF6E7B66")]
GLASS_ON = [(0.0, "#FFFFF0C0"), (0.55, "#FFF2C14E"), (1.0, "#FFD79A2A")]
HALO = [(0.0, "#66F2C14E"), (0.6, "#22F2C14E"), (1.0, "#00F2C14E")]

BOOTH_BASE = "M6,182 L134,182 L138,197 L2,197 Z"
BOOTH_BODY = "M18,38 L122,38 L122,183 L18,183 Z"
BOOTH_ROOF = "M8,14 C8,12 10,10 12,10 L128,10 C130,10 132,12 132,14 L122,38 L18,38 Z"
BOOTH_SIGN = "M32,17 L108,17 L108,31 L32,31 Z"
WINDOW = "M30,50 L110,50 L110,120 L30,120 Z"
MULLION = "M69,50 L71,50 L71,120 L69,120 Z M30,84 L110,84 L110,86 L30,86 Z"
REFLECT = "M38,118 L62,52 L74,52 L50,118 Z"
DOOR_PANEL = "M34,132 L106,132 L106,170 L34,170 Z"


def booth(lit: bool) -> str:
    parts = [
        path(BOOTH_BASE, fill="#FF33220F"),
        path(BOOTH_BODY, gradient=linear(18, 38, 122, 183, GREEN), stroke=INK, width=3),
        path(BOOTH_ROOF, gradient=linear(8, 10, 132, 38, ROOF), stroke=INK, width=3),
        path(BOOTH_SIGN, fill="@color/mustard", stroke=INK, width=2),
    ]
    # «Буквы» на вывеске: штрихи, читается как надпись, но ничего не обещает
    for x in range(38, 100, 10):
        parts.append(path(f"M{x},20 L{x + 5},20 L{x + 5},28 L{x},28 Z", fill=INK,
                          fill_alpha=0.75))

    if lit:
        parts.append(path(ellipse(70, 85, 70, 62),
                          gradient=radial(70, 85, 70, HALO)))

    parts += [
        path(WINDOW,
             gradient=(radial(56, 70, 68, GLASS_ON) if lit
                       else linear(30, 50, 110, 120, GLASS_OFF)),
             stroke=INK, width=3),
        path(REFLECT, fill="#FFFFFFFF", fill_alpha=0.16 if lit else 0.10),
        path(MULLION, fill=INK),
        path(DOOR_PANEL, stroke=INK, width=2),
        path(ellipse(98, 151, 5, 5), fill="@color/mustard", stroke=INK, width=2),
        path(ellipse(96.5, 149.5, 1.8, 1.8), fill="#FFFFFFFF", fill_alpha=0.7),
    ]

    state = "окно светится" if lit else "окно тёмное"
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f"<!-- Телефонная будка, {state}. Файл сгенерирован tools/draw_art.py. -->\n"
        '<vector xmlns:android="http://schemas.android.com/apk/res/android"\n'
        '    xmlns:aapt="http://schemas.android.com/aapt"\n'
        '    android:width="112dp"\n'
        '    android:height="160dp"\n'
        '    android:viewportWidth="140"\n'
        '    android:viewportHeight="200">\n\n'
        + "".join(parts)
        + "</vector>\n"
    )


# --- иконка приложения ------------------------------------------------------


def icon() -> str:
    """Голова Гены в шляпе, ужатая в безопасную зону адаптивной иконки."""
    inner = "".join([
        path(GENA_HEAD, gradient=radial(84, 66, 130, GREEN_SKIN), stroke=INK, width=6),
        path(GENA_JAW, gradient=linear(34, 108, 94, 134, GREEN_JAW), stroke=INK, width=4),
        soft_light(96, 74, 30, 20),
        path(NOSTRIL, fill="#FF23331F"),
        path(MOUTH_SMILE, stroke=INK, width=5),
        gena_eye(101, 48, -3, -1),
        gena_eye(139, 54, -3, -1),
        '    <group\n        android:pivotX="98"\n        android:pivotY="34"\n'
        '        android:rotation="-11">\n',
        path(HAT_BRIM, gradient=linear(54, 34, 142, 48, HAT), stroke=INK, width=5),
        path(HAT_CROWN, gradient=linear(72, 9, 124, 44, HAT), stroke=INK, width=5),
        path(HAT_BAND, fill="#FF0F0C0A", fill_alpha=0.55),
        "    </group>\n",
    ])
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<!-- Иконка: голова Гены. Файл сгенерирован tools/draw_art.py. -->\n"
        '<vector xmlns:android="http://schemas.android.com/apk/res/android"\n'
        '    xmlns:aapt="http://schemas.android.com/aapt"\n'
        '    android:width="108dp"\n'
        '    android:height="108dp"\n'
        '    android:viewportWidth="108"\n'
        '    android:viewportHeight="108">\n\n'
        '    <group\n'
        '        android:pivotX="95"\n'
        '        android:pivotY="75"\n'
        '        android:scaleX="0.44"\n'
        '        android:scaleY="0.44"\n'
        '        android:translateX="-41"\n'
        '        android:translateY="-21">\n'
        + inner
        + "    </group>\n</vector>\n"
    )


if __name__ == "__main__":
    (RES / "hero_off.xml").write_text(gena(False))
    (RES / "hero_on.xml").write_text(gena(True))
    (RES / "booth_off.xml").write_text(booth(False))
    (RES / "booth_on.xml").write_text(booth(True))
    (RES / "ic_launcher_foreground.xml").write_text(icon())
    print("нарисованы герой, будка и иконка")

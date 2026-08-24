"""KeyEventClassifier state machine: shift/caps/numlock/ctrl, per-device state."""
from dusky_keylogger import keycodes as kc
from dusky_keylogger.listener import KeyEventClassifier

N = "TestKb"


def press(cl, code, value=1, ts=1000, dev="d0"):
    return cl.handle(dev, N, code, value, ts)


def test_plain_and_shifted():
    cl = KeyEventClassifier()
    assert press(cl, kc.KEY_H).char == "h"
    press(cl, kc.KEY_LEFTSHIFT, 1)
    assert press(cl, kc.KEY_H).char == "H"
    press(cl, kc.KEY_LEFTSHIFT, 0)
    assert press(cl, kc.KEY_H).char == "h"


def test_repeat_and_release_dropped():
    cl = KeyEventClassifier()
    assert press(cl, kc.KEY_H, 2) is None
    assert press(cl, kc.KEY_H, 0) is None


def test_capslock_toggles_each_press():
    cl = KeyEventClassifier()
    press(cl, kc.KEY_CAPSLOCK, 1)
    assert press(cl, kc.KEY_H).char == "H"
    press(cl, kc.KEY_CAPSLOCK, 1)
    assert press(cl, kc.KEY_H).char == "h"


def test_shortcut_modifier_suppression():
    cl = KeyEventClassifier()
    press(cl, kc.KEY_LEFTCTRL, 1)
    assert press(cl, kc.KEY_C) is None
    assert press(cl, kc.KEY_BACKSPACE).kind == "backspace"
    assert press(cl, kc.KEY_DELETE) is None
    press(cl, kc.KEY_LEFTCTRL, 0)
    assert press(cl, kc.KEY_C).char == "c"


def test_per_device_isolation():
    cl = KeyEventClassifier()
    press(cl, kc.KEY_LEFTSHIFT, 1, dev="d1")
    assert press(cl, kc.KEY_A, dev="d2").char == "a"
    assert press(cl, kc.KEY_A, dev="d1").char == "A"


def test_sync_from_kernel():
    cl = KeyEventClassifier()
    cl.sync_from_kernel("dx", [kc.KEY_LEFTSHIFT], False, False)
    st = cl.state_for("dx")
    assert st.shift_pressed and not st.shortcut_modifiers
    assert st.num_on is False and st.num_from_led
    p = cl.handle("dx", N, kc.KEY_KP5, 1, 6)
    assert p.kind == "navigation" and p.char is None
    cl.handle_led("dx", kc.LED_NUML, 1)
    p = cl.handle("dx", N, kc.KEY_KP5, 1, 7)
    assert p.char == "5" and p.kind == "printable"


def test_reset_clears_device_state():
    cl = KeyEventClassifier()
    press(cl, kc.KEY_LEFTSHIFT, 1, dev="d1")
    cl.reset("d1")
    assert not cl.state_for("d1").shift_pressed

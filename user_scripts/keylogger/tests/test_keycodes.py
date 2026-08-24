"""US-layout character resolution and key classification."""
from dusky_keylogger import keycodes as kc


def test_letter_shift_xor_caps():
    assert kc.char_for(kc.KEY_A, False, False) == "a"
    assert kc.char_for(kc.KEY_A, True, False) == "A"
    assert kc.char_for(kc.KEY_A, False, True) == "A"
    assert kc.char_for(kc.KEY_A, True, True) == "a"


def test_punct_shift_only():
    assert kc.char_for(kc.KEY_1, True, True) == "!"
    assert kc.char_for(kc.KEY_EQUAL, True, False) == "+"
    assert kc.char_for(kc.KEY_SLASH, False, False) == "/"
    assert kc.char_for(kc.KEY_102ND, True, False) == "|"


def test_keypad_numlock():
    assert kc.char_for(kc.KEY_KP5, False, False, numlock=True) == "5"
    assert kc.char_for(kc.KEY_KP5, False, False, numlock=False) is None
    assert kc.char_for(kc.KEY_KPDOT, False, False, numlock=False) is None
    # operators always emit
    assert kc.char_for(kc.KEY_KPPLUS, False, False, numlock=False) == "+"
    assert kc.char_for(kc.KEY_KPSLASH, False, False, numlock=True) == "/"


def test_non_printables_have_no_char():
    for code in (kc.KEY_LEFTCTRL, kc.KEY_F5, kc.KEY_ESC, kc.KEY_KPENTER):
        assert kc.char_for(code, False, False) is None


def test_classify_key_kinds():
    cases = {
        kc.KEY_BACKSPACE: kc.Kind.BACKSPACE,
        kc.KEY_DELETE: kc.Kind.DELETE,
        kc.KEY_ENTER: kc.Kind.ENTER,
        kc.KEY_KPENTER: kc.Kind.ENTER,
        kc.KEY_TAB: kc.Kind.TAB,
        kc.KEY_ESC: kc.Kind.ESCAPE,
        kc.KEY_LEFTSHIFT: kc.Kind.MODIFIER,
        kc.KEY_CAPSLOCK: kc.Kind.MODIFIER,
        kc.KEY_F12: kc.Kind.FUNCTION,
        kc.KEY_UP: kc.Kind.NAVIGATION,
        kc.KEY_Z: kc.Kind.PRINTABLE,
        kc.KEY_SPACE: kc.Kind.PRINTABLE,
        kc.KEY_MUTE: kc.Kind.OTHER,
    }
    for code, kind in cases.items():
        assert kc.classify_key(code) == kind


def test_key_names_roundtrip():
    assert kc.key_name(kc.KEY_A) == "KEY_A"
    assert kc.key_name(9999) == "KEY_9999"
    assert kc.KEY_A == 30
    assert kc.KEY_F24 == 194
    assert len(kc.KEY_NAMES) > 200

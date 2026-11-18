from gui.widgets.diagnostic_feedback import DiagnosticFeedbackDialog, _select_bundle_path


def test_save_dialog_uses_non_native_mode(app, monkeypatch, tmp_path):
    seen = {}
    class FakeDialog:
        Accepted = 1
        DontUseNativeDialog = 2
        AcceptSave = 3
        AnyFile = 4
        def __init__(self, *args): pass
        def setAcceptMode(self, value): seen['accept'] = value
        def setDefaultSuffix(self, value): seen['suffix'] = value
        def setOption(self, option, enabled): seen['native'] = (option, enabled)
        def setFileMode(self, value): seen['mode'] = value
        def exec(self): return 0
        def selectedFiles(self): return []
    monkeypatch.setattr('gui.widgets.diagnostic_feedback.QFileDialog', FakeDialog)
    dialog = DiagnosticFeedbackDialog.__new__(DiagnosticFeedbackDialog)
    dialog._io_worker = None
    dialog.start()
    assert seen['native'] == (FakeDialog.DontUseNativeDialog, True)


def test_cancelled_save_dialog_does_not_return_path(monkeypatch):
    class FakeDialog:
        Accepted = 1
        DontUseNativeDialog = 2
        AcceptSave = 3
        AnyFile = 4
        def __init__(self, *args): pass
        def setAcceptMode(self, value): pass
        def setDefaultSuffix(self, value): pass
        def setOption(self, option, enabled): pass
        def setFileMode(self, value): pass
        def exec(self): return 0
        def selectedFiles(self): raise AssertionError('cancelled dialogs must not read a path')
    monkeypatch.setattr('gui.widgets.diagnostic_feedback.QFileDialog', FakeDialog)
    monkeypatch.setattr('gui.widgets.diagnostic_feedback.os.name', 'nt')
    assert _select_bundle_path(None) == ''

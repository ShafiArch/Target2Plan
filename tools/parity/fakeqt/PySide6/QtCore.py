class QObject:
    def __init__(self, *a, **k):
        pass


class _Sig:
    def emit(self, *a):
        pass

    def connect(self, *a):
        pass


def Signal(*a, **k):
    return _Sig()

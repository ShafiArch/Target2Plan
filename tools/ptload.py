"""Minimal torch-free reader for PyTorch zip checkpoints (.pt).

Rebuilds every tensor as a numpy array so the weights can be inspected
and exported without installing torch.
"""
import collections
import pickle
import sys
import zipfile

import numpy as np

_DTYPES = {
    "FloatStorage": np.float32, "DoubleStorage": np.float64, "HalfStorage": np.float16,
    "LongStorage": np.int64, "IntStorage": np.int32, "ShortStorage": np.int16,
    "CharStorage": np.int8, "ByteStorage": np.uint8, "BoolStorage": np.bool_,
    "BFloat16Storage": np.uint16,
}


class _StorageType:
    def __init__(self, name):
        self.name = name


def _rebuild_tensor_v2(storage, offset, size, stride, requires_grad=False, hooks=None, metadata=None):
    arr = storage
    if len(size) == 0:
        return np.array(arr[offset])
    item = arr.itemsize
    return np.lib.stride_tricks.as_strided(arr[offset:], shape=tuple(size),
                                           strides=tuple(s * item for s in stride)).copy()


def _rebuild_parameter(data, requires_grad=False, hooks=None):
    return data


class _Unpickler(pickle.Unpickler):
    def __init__(self, f, zf, prefix):
        super().__init__(f)
        self.zf, self.prefix = zf, prefix

    def find_class(self, module, name):
        if module == "collections" and name == "OrderedDict":
            return collections.OrderedDict
        if module == "torch._utils" and name == "_rebuild_tensor_v2":
            return _rebuild_tensor_v2
        if module == "torch._utils" and name == "_rebuild_parameter":
            return _rebuild_parameter
        if module == "torch" and name.endswith("Storage"):
            return _StorageType(name)
        if module.startswith("numpy"):
            return super().find_class(module, name)
        if module == "builtins":
            return super().find_class(module, name)
        if module == "torch" and name in ("float32", "float64", "int64"):
            return name
        raise pickle.UnpicklingError(f"refusing to load {module}.{name}")

    def persistent_load(self, pid):
        kind, stype, key, _loc, _numel = pid
        assert kind == "storage"
        dtype = _DTYPES[stype.name]
        raw = self.zf.read(f"{self.prefix}/data/{key}")
        return np.frombuffer(raw, dtype=dtype)


def load(path):
    zf = zipfile.ZipFile(path)
    pkl = next(n for n in zf.namelist() if n.endswith("data.pkl"))
    prefix = pkl[: -len("/data.pkl")]
    with zf.open(pkl) as f:
        return _Unpickler(f, zf, prefix).load()


if __name__ == "__main__":
    for p in sys.argv[1:]:
        ck = load(p)
        print("==", p)
        sd = ck.get("model_state", ck) if isinstance(ck, dict) else ck
        for k, v in ck.items():
            if k != "model_state":
                print("  meta", k, (v if not isinstance(v, dict) or len(str(v)) < 400 else str(v)[:400] + "…"))
        total = 0
        for k, v in sd.items():
            total += v.size
            print(f"  {k:32s} {str(v.dtype):8s} {tuple(v.shape)}")
        print("  params:", total)

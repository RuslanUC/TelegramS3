from hashlib import md5

def patch_binaryio(io):
    m = md5()
    oread = io.read
    def _read(*args, **kwargs):
        data = oread(*args, **kwargs)
        m.update(data)
        return data
    return m

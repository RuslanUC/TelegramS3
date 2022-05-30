from hmac import new
from hashlib import sha256

class SignatureV4:
    def __init__(self, request):
        self.userId = None
        self._valid = True
        self.verified = False
        self._request = request
        if not (auth := request.headers.get("Authorization")):
            self._valid = False
            return
        auth = auth.replace(",", "").split(" ")
        auth.remove("AWS4-HMAC-SHA256")
        auth = dict([a.split("=") for a in auth])
        if not (cred := auth.get("Credential", "").split("/")) or not (sh := auth.get("SignedHeaders")) or not (sig := auth.get("Signature")):
            self._valid = False
            return
        self.userId = cred[0]
        self.datestamp = cred[1]
        self.region = cred[2]
        self.service = cred[3]
        self.signedHeaders = sh.split(";")
        self.signature = sig
        self.amzdate = request.headers.get("x-amz-date")

    def _sign(self, key, msg):
        return new(key, msg.encode('utf-8'), sha256).digest()

    def _getSignatureKey(self, key):
        kDate = self._sign(('AWS4' + key).encode('utf-8'), self.datestamp)
        kRegion = self._sign(kDate, self.region)
        kService = self._sign(kRegion, self.service)
        kSigning = self._sign(kService, 'aws4_request')
        return kSigning

    def _getQueryString(self):
        
        qs = [s.split("=")+[""] for s in qs.split("&")]
        qs = ["=".join(s[0:2]) for s in qs]
        return "&".join(qs)

    def _getQueryString(self):
        query_string = ''
        if (qs := self._request.query_string.decode()):
            key_val_pairs = []
            for pair in qs.split('&'):
                key, _, value = pair.partition('=')
                key_val_pairs.append((key, value))
            sorted_key_vals = []
            for key, value in sorted(key_val_pairs):
                sorted_key_vals.append(f'{key}={value}')
            query_string = '&'.join(sorted_key_vals)
        return query_string

    def verify(self, key):
        if not self._valid:
            return False
        req = []
        req.append(self._request.method)
        req.append(self._request.path)
        req.append(self._getQueryString())
        req.append("\n".join([f"{name}:{self._request.headers.get(name)}" for name in self.signedHeaders])+"\n")
        req.append(";".join(self.signedHeaders))
        req.append(self._request.headers.get("x-amz-content-sha256"))
        req = "\n".join(req).encode('utf-8')
        s = f'AWS4-HMAC-SHA256\n{self.amzdate}\n{self.datestamp}/{self.region}/{self.service}/aws4_request\n{sha256(req).hexdigest()}'
        key = self._getSignatureKey(key)
        signature = new(key, s.encode('utf-8'), sha256).hexdigest()
        self.verified = signature == self.signature
        return self.verified
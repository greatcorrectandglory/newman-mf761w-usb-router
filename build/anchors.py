from pathlib import Path
import re
import sys
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID

roots = Path(sys.argv[1]).read_bytes()
dest = Path(sys.argv[2])
declarations, anchors = [], []
def byte_array(name, value):
    declarations.append("static unsigned char %s[] = {%s};" % (name, ",".join("0x%02x"%b for b in value)))
    return "%s,sizeof(%s)" % (name, name)
def magnitude(value):
    return value.to_bytes((value.bit_length()+7)//8, "big")
for pem in re.findall(b"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",roots,re.S):
    cert=x509.load_pem_x509_certificate(pem)
    cn=cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    if not cn or not re.search(r"GTS Root|GlobalSign|ISRG Root|DigiCert|Amazon Root|Microsoft .*Root|Baltimore CyberTrust|USERTrust|Sectigo|SSL.com|TWCA Global|Entrust", cn[0].value,re.I):continue
    key=cert.public_key(); prefix="anchor_%d_"%len(anchors)
    if isinstance(key,rsa.RSAPublicKey):
        n=key.public_numbers()
        union="BR_KEYTYPE_RSA,{.rsa={%s,%s}}"%(byte_array(prefix+"n",magnitude(n.n)),byte_array(prefix+"e",magnitude(n.e)))
    elif isinstance(key,ec.EllipticCurvePublicKey) and key.curve.name in ("secp256r1","secp384r1","secp521r1"):
        q=key.public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
        union="BR_KEYTYPE_EC,{.ec={BR_EC_%s,%s}}"%(key.curve.name,byte_array(prefix+"q",q))
    else:continue
    anchors.append("{{%s},BR_X509_TA_CA,{%s}}"%(byte_array(prefix+"dn",cert.subject.public_bytes()),union))
if len(anchors)<12: raise SystemExit("Incomplete CA bundle")
dest.write_text("\n".join(declarations)+"\nstatic const br_x509_trust_anchor anchors[] = {\n"+",\n".join(anchors)+"\n};\n",encoding="ascii")
print("Trusted public CA roots for probe hosts:",len(anchors))

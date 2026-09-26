"""Offline GD101 discovery signature checking. Requires PyCryptodome.

This authenticates only the 24 identity bytes covered by the observed driver
call. It does not authenticate the whole reply, negotiate a session, or do I/O.
The caller must supply a trusted public key, never one taken from the reply.
"""
import hashlib

from gd101_native import parse_discovery_reply


def decode_field_b(field, signature_a):
    """Recover the second 64-byte signature using the observed buffer transform."""
    field = bytes(field)
    if len(field) != 64:
        raise ValueError('Field B must contain 64 bytes')
    signature_a = bytes(signature_a)
    if len(signature_a) != 64:
        raise ValueError('Signature A must contain 64 bytes')
    count, bits, mask = signature_a[1] % 64, signature_a[2] % 8, signature_a[0]
    rotated = field[-count:] + field[:-count] if count else field
    decoded = bytes((((x >> bits) | (x << (8-bits))) & 255) ^ mask for x in rotated)
    return decoded[:32][::-1] + decoded[32:][::-1]


def encode_field_b(signature, signature_a):
    """Inverse representation transform. This does not create a signature."""
    signature = bytes(signature)
    if len(signature) != 64:
        raise ValueError('Signature must contain 64 bytes')
    signature_a = bytes(signature_a)
    if len(signature_a) != 64:
        raise ValueError('Signature A must contain 64 bytes')
    count, bits, mask = signature_a[1] % 64, signature_a[2] % 8, signature_a[0]
    reversed_halves = signature[:32][::-1] + signature[32:][::-1]
    rotated = bytes((((x ^ mask) << bits) | ((x ^ mask) >> (8-bits))) & 255
                    for x in reversed_halves)
    return rotated[count:] + rotated[:count]


class _LittleEndianSHA256:
    oid = '2.16.840.1.101.3.4.2.1'
    digest_size = 32

    def __init__(self, digest):
        self._digest = digest

    def digest(self):
        # DSS interprets bytes as big-endian; the observed driver uses little.
        return self._digest[::-1]


def verify_p256_signature(public_key, digest, signature):
    """Verify the driver's little-endian P-256 format, returning a boolean."""
    from Crypto.PublicKey import ECC
    from Crypto.Signature import DSS
    public_key, digest, signature = map(bytes, (public_key, digest, signature))
    if (len(public_key), len(digest), len(signature)) != (64, 32, 64):
        raise ValueError('Expected 64-byte public key, 32-byte digest, 64-byte signature')
    try:
        key = ECC.construct(curve='P-256',
                            point_x=int.from_bytes(public_key[:32], 'little'),
                            point_y=int.from_bytes(public_key[32:], 'little'))
        converted = signature[:32][::-1] + signature[32:][::-1]
        DSS.new(key, 'fips-186-3', encoding='binary').verify(
            _LittleEndianSHA256(digest), converted)
    except ValueError:
        return False
    return True


def verify_discovery_signature(payload, trusted_public_key):
    """Check field A against SHA256(identifier || field_16).

    Field B and the trailing byte are NOT covered by this signature check.
    Passing this function alone does not establish a valid startup exchange.
    """
    fields = parse_discovery_reply(payload)
    digest = hashlib.sha256(fields['identifier'] + fields['field_16']).digest()
    return verify_p256_signature(trusted_public_key, digest, fields['field_64_a'])


def verify_discovery_identity(payload, expected_identifier, trusted_key_a, trusted_key_b):
    """Check identifier and both observed signatures. Does not open a session.

    Both public keys must be trusted independently of the reply. This verifies
    identity material only; it does not derive the framing key or authenticate
    arbitrary later messages.
    """
    expected_identifier = bytes(expected_identifier)
    if len(expected_identifier) != 8:
        raise ValueError('Expected identifier must contain exactly 8 bytes')
    fields = parse_discovery_reply(payload)
    if fields['identifier'] != expected_identifier:
        return False
    digest = hashlib.sha256(fields['identifier'] + fields['field_16']).digest()
    return (verify_p256_signature(trusted_key_a, digest, fields['field_64_a'])
            and verify_p256_signature(trusted_key_b, digest,
                                      decode_field_b(fields['field_64_b'], fields['field_64_a'])))


def derive_framing_key(host_signature, shared_secret, verified_reply):
    """Derive the 32-byte wire-order XOR key from an already verified reply.

    This function does not authenticate the supplied reply. Call
    verify_discovery_identity first with independently trusted public keys.
    The shared secret is the 32-byte little-endian ECDH output observed in the
    driver; host_signature is the exact outgoing 64-byte signature.
    """
    host_signature, shared_secret = bytes(host_signature), bytes(shared_secret)
    if len(host_signature) != 64 or len(shared_secret) != 32:
        raise ValueError('Expected 64-byte host signature and 32-byte shared secret')
    fields = parse_discovery_reply(verified_reply)
    second = decode_field_b(fields['field_64_b'], fields['field_64_a'])
    second_big_endian = second[:32][::-1] + second[32:][::-1]
    material = host_signature + shared_secret + fields['field_64_a'] + second_big_endian
    return hashlib.sha256(material).digest()[::-1]


def sign_host_identity(private_key, identifier):
    """Create the host's little-endian ECDSA signature using OS randomness."""
    from Crypto.PublicKey import ECC
    from Crypto.Signature import DSS
    private_key, identifier = bytes(private_key), bytes(identifier)
    if len(private_key) != 32 or len(identifier) != 8:
        raise ValueError('Expected 32-byte private key and 8-byte identifier')
    key = ECC.construct(curve='P-256', d=int.from_bytes(private_key, 'little'))
    digest = hashlib.sha256(identifier).digest()
    signature = DSS.new(key, 'fips-186-3', encoding='binary').sign(_LittleEndianSHA256(digest))
    return signature[:32][::-1] + signature[32:][::-1]


def shared_secret(private_key, peer_public_key):
    """Return P-256 ECDH x-coordinate in the original driver's byte order."""
    from Crypto.PublicKey import ECC
    private_key, peer_public_key = bytes(private_key), bytes(peer_public_key)
    if len(private_key) != 32 or len(peer_public_key) != 64:
        raise ValueError('Expected 32-byte private key and 64-byte peer public key')
    private = ECC.construct(curve='P-256', d=int.from_bytes(private_key, 'little'))
    peer = ECC.construct(curve='P-256', point_x=int.from_bytes(peer_public_key[:32], 'little'),
                         point_y=int.from_bytes(peer_public_key[32:], 'little'))
    return int((peer.pointQ * private.d).x).to_bytes(32, 'little')

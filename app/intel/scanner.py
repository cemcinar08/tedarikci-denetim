"""Dış istihbarat tarayıcısı — tedarikçi domain'leri için PASİF güvenlik değerlendirmesi.

Yalnızca herkese açık kaynaklar ve normal (bir tarayıcının yapacağı ölçüde)
bağlantılar kullanılır; port taraması, zafiyet istismarı veya kaba kuvvet YOKTUR.

Kaynaklar:
  * DNS (sistem çözümleyici, olmazsa DNS-over-HTTPS / dns.google)
  * TLS el sıkışması (443)
  * HTTP/HTTPS ana sayfa yanıt başlıkları, /.well-known/security.txt
  * crt.sh  — Sertifika Şeffaflığı kayıtlarından alt alan adları
  * Shodan InternetDB — IP başına bilinen açık portlar ve CVE'ler (ücretsiz, anahtarsız)
  * RDAP   — domain kayıt / bitiş tarihi
"""
from __future__ import annotations

import asyncio
import datetime as dt
import ipaddress
import re
import socket
import ssl
import warnings
from dataclasses import asdict, dataclass, field

import dns.asyncresolver
import dns.exception
import dns.resolver
import httpx

TIMEOUT = 10.0
UA = "TedarikciDenetim-PassiveScanner/1.0"

# status: pass | warn | fail | info | error
STATUS_POINTS = {"pass": 1.0, "warn": 0.5, "fail": 0.0}

RISKY_PORTS = {
    21: "FTP", 23: "Telnet", 25: "SMTP", 110: "POP3", 135: "MS-RPC", 139: "NetBIOS",
    445: "SMB", 1433: "MSSQL", 1521: "Oracle DB", 2375: "Docker API", 3306: "MySQL",
    3389: "RDP", 5432: "PostgreSQL", 5900: "VNC", 5984: "CouchDB", 6379: "Redis",
    8080: "HTTP-alt", 9200: "Elasticsearch", 11211: "Memcached", 27017: "MongoDB",
}
# 25 (SMTP) bir mail sunucusunda normaldir; MX kayıtlarındaki IP'lerde risk sayılmaz.

DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?!-)([a-z0-9-]{1,63}(?<!-)\.)+[a-z]{2,63}$")


@dataclass
class Check:
    id: str
    category: str
    title: str
    status: str
    detail: str = ""
    weight: int = 5
    severity: str = "orta"          # bulgu oluşturulursa kullanılacak önem
    recommendation: str = ""
    data: dict = field(default_factory=dict)


def normalize_domain(value: str) -> str:
    v = value.strip().lower()
    v = re.sub(r"^[a-z]+://", "", v)
    v = v.split("/")[0].split(":")[0].rstrip(".")
    if v.startswith("www."):
        v = v[4:]
    v = v.encode("idna").decode("ascii") if any(ord(c) > 127 for c in v) else v
    if not DOMAIN_RE.match(v):
        raise ValueError(f"Geçersiz domain: {value!r}")
    return v


# --------------------------------------------------------------------------- DNS
class Resolver:
    """Önce sistem DNS'i, başarısız olursa DNS-over-HTTPS kullanır."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.res = dns.asyncresolver.Resolver()
        self.res.lifetime = 5
        self.use_doh = False

    async def query(self, name: str, rtype: str) -> list[str]:
        if not self.use_doh:
            try:
                ans = await self.res.resolve(name, rtype)
                return [r.to_text() for r in ans]
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
                return []
            except (dns.exception.Timeout, dns.resolver.NoNameservers, OSError):
                self.use_doh = True  # ağ DNS'e izin vermiyorsa DoH'a geç
        r = await self.client.get("https://dns.google/resolve", params={"name": name, "type": rtype})
        r.raise_for_status()
        j = r.json()
        want = dns.rdatatype.from_text(rtype)
        return [a["data"] for a in j.get("Answer", []) if a.get("type") == want]


def _txt(values: list[str]) -> list[str]:
    # TXT kayıtları "a" "b" biçiminde parçalı gelebilir
    out = []
    for v in values:
        parts = re.findall(r'"((?:[^"\\]|\\.)*)"', v)
        out.append("".join(parts) if parts else v)
    return out


async def check_dns(domain: str, r: Resolver) -> tuple[list[Check], dict]:
    checks: list[Check] = []
    names = [(domain, "A"), (domain, "AAAA"), (domain, "MX"), (domain, "NS"), (domain, "TXT"),
             (f"_dmarc.{domain}", "TXT"), (domain, "CAA"), (domain, "DNSKEY")]

    async def fetch_all():
        res = await asyncio.gather(*(r.query(n, t) for n, t in names), return_exceptions=True)
        return [[] if isinstance(x, Exception) else x for x in res], any(isinstance(x, Exception) for x in res)

    (a, aaaa, mx, ns, txt, dmarc, caa, dnskey), had_err = await fetch_all()
    # Var olan her domain'in NS kaydı vardır. Yoksa çözümleyici güvenilmezdir
    # (kurumsal/split DNS, filtreleyen proxy vb.) -> DoH ile tekrar dene.
    if not ns and not r.use_doh:
        r.use_doh = True
        (a, aaaa, mx, ns, txt, dmarc, caa, dnskey), had_err = await fetch_all()
    if not ns:
        msg = ("DNS yanıtları alınamadı veya güvenilir değil (NS kaydı dönmedi). "
               "Domain mevcut değil ya da ağ DNS sorgularını engelliyor; DNS/e-posta kontrolleri puanlanmadı.")
        return [Check("dns.resolve", "DNS", "DNS sorgusu", "error", msg, weight=0)], \
            {"a": [x for x in a if re.match(r"^\d+\.\d+\.\d+\.\d+$", x)], "aaaa": aaaa, "mx": [], "ns": [], "dns_ok": False}
    txt, dmarc = _txt(txt), _txt(dmarc)
    mx_hosts = sorted({m.split()[-1].rstrip(".") for m in mx if m.split()})

    info = {"a": a, "aaaa": aaaa, "mx": mx_hosts, "ns": [n.rstrip(".") for n in ns], "dns_ok": True}
    checks.append(Check("dns.records", "DNS", "DNS kayıtları", "info",
                        f"A: {', '.join(a) or '-'} | MX: {', '.join(mx_hosts) or '-'} | NS: {', '.join(info['ns']) or '-'}",
                        weight=0, data=info))

    # SPF
    spf = [t for t in txt if t.lower().startswith("v=spf1")]
    if not spf:
        checks.append(Check("email.spf", "E-posta Güvenliği", "SPF kaydı", "fail",
                            "SPF kaydı bulunamadı; domain adına sahte e-posta gönderilebilir.",
                            weight=10, severity="yüksek",
                            recommendation="'v=spf1 ... -all' biçiminde bir SPF TXT kaydı yayınlayın."))
    elif len(spf) > 1:
        checks.append(Check("email.spf", "E-posta Güvenliği", "SPF kaydı", "fail",
                            "Birden fazla SPF kaydı var (RFC 7208'e göre geçersiz).", weight=10, severity="orta",
                            recommendation="SPF kayıtlarını tek bir kayıtta birleştirin.", data={"spf": spf}))
    else:
        s = spf[0].lower()
        if "+all" in s or "?all" in s or not re.search(r"[~-]all", s):
            checks.append(Check("email.spf", "E-posta Güvenliği", "SPF kaydı", "warn",
                                f"SPF politikası gevşek: {spf[0]}", weight=10, severity="orta",
                                recommendation="SPF kaydını '-all' veya '~all' ile sonlandırın.", data={"spf": spf}))
        else:
            checks.append(Check("email.spf", "E-posta Güvenliği", "SPF kaydı", "pass", spf[0], weight=10))

    # DMARC
    dm = [t for t in dmarc if t.lower().startswith("v=dmarc1")]
    if not dm:
        checks.append(Check("email.dmarc", "E-posta Güvenliği", "DMARC politikası", "fail",
                            "DMARC kaydı yok; oltalama (phishing) e-postalarına karşı koruma yok.",
                            weight=10, severity="yüksek",
                            recommendation="_dmarc TXT kaydı yayınlayın; p=quarantine veya p=reject hedefleyin."))
    else:
        m = re.search(r"\bp\s*=\s*(\w+)", dm[0], re.I)
        p = (m.group(1).lower() if m else "none")
        st = "pass" if p in ("quarantine", "reject") else "warn"
        checks.append(Check("email.dmarc", "E-posta Güvenliği", "DMARC politikası", st,
                            f"p={p} — {dm[0]}", weight=10, severity="orta",
                            recommendation="" if st == "pass" else "DMARC politikasını p=none'dan quarantine/reject seviyesine yükseltin."))

    # DNSSEC
    checks.append(Check("dns.dnssec", "DNS", "DNSSEC", "pass" if dnskey else "warn",
                        "DNSKEY kaydı mevcut." if dnskey else "DNSSEC etkin değil; DNS sahteciliğine karşı ek koruma yok.",
                        weight=3, severity="düşük",
                        recommendation="" if dnskey else "Kayıt firması / DNS sağlayıcısı üzerinden DNSSEC'i etkinleştirin."))
    # CAA
    checks.append(Check("dns.caa", "DNS", "CAA kaydı", "pass" if caa else "warn",
                        ", ".join(caa) if caa else "CAA kaydı yok; herhangi bir sertifika otoritesi sertifika üretebilir.",
                        weight=2, severity="düşük",
                        recommendation="" if caa else "Kullandığınız CA'ları belirten CAA kayıtları ekleyin."))

    if not a and not aaaa:
        checks.append(Check("dns.resolve", "DNS", "Domain çözümlemesi", "error",
                            "Domain için A/AAAA kaydı bulunamadı.", weight=0))
    return checks, info


# --------------------------------------------------------------------------- TLS
def _tls_probe(host: str) -> dict:
    ctx = ssl.create_default_context()
    out: dict = {}
    try:
        with socket.create_connection((host, 443), timeout=TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as s:
                cert = s.getpeercert()
                out.update(valid=True, version=s.version(), cipher=s.cipher()[0], cert=cert)
    except ssl.SSLCertVerificationError as e:
        out.update(valid=False, error=e.verify_message or str(e))
    except (ssl.SSLError, OSError) as e:
        out.update(valid=None, error=str(e))
        return out

    # Eski protokol (TLS 1.0/1.1) kabul ediliyor mu?
    legacy = {}
    for name, ver in (("TLSv1", ssl.TLSVersion.TLSv1), ("TLSv1.1", ssl.TLSVersion.TLSv1_1)):
        c = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        c.check_hostname = False
        c.verify_mode = ssl.CERT_NONE
        try:
            c.set_ciphers("DEFAULT:@SECLEVEL=0")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                c.minimum_version = ver
                c.maximum_version = ver
        except (ValueError, ssl.SSLError):
            legacy[name] = None
            continue
        try:
            with socket.create_connection((host, 443), timeout=TIMEOUT) as sock:
                with c.wrap_socket(sock, server_hostname=host):
                    legacy[name] = True
        except ssl.SSLError as e:
            # istemci tarafı bu protokolü hiç desteklemiyorsa test edilemez
            legacy[name] = None if "NO_PROTOCOLS_AVAILABLE" in str(e).upper() else False
        except OSError:
            legacy[name] = False
    out["legacy"] = legacy
    return out


async def check_tls(host: str) -> list[Check]:
    res = await asyncio.to_thread(_tls_probe, host)
    checks: list[Check] = []
    if res.get("valid") is None:
        return [Check("tls.connect", "TLS / HTTPS", "HTTPS (443) bağlantısı", "error",
                      f"443 portuna TLS bağlantısı kurulamadı: {res.get('error')}", weight=0)]
    if res["valid"] is False:
        checks.append(Check("tls.cert", "TLS / HTTPS", "TLS sertifikası", "fail",
                            f"Sertifika doğrulanamadı: {res.get('error')}", weight=15, severity="yüksek",
                            recommendation="Güvenilir bir CA'dan, domain adıyla eşleşen geçerli bir sertifika kullanın."))
    else:
        cert = res["cert"]
        not_after = dt.datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=dt.timezone.utc)
        days = (not_after - dt.datetime.now(dt.timezone.utc)).days
        issuer = dict(x[0] for x in cert.get("issuer", ()))
        issuer_name = issuer.get("organizationName") or issuer.get("commonName", "?")
        detail = f"Veren: {issuer_name} | Bitiş: {not_after:%Y-%m-%d} ({days} gün) | {res['version']} / {res['cipher']}"
        if days < 0:
            st, sev, rec = "fail", "kritik", "Süresi dolmuş sertifikayı hemen yenileyin."
        elif days < 21:
            st, sev, rec = "warn", "orta", "Sertifika yakında sona eriyor; otomatik yenileme kurun."
        else:
            st, sev, rec = "pass", "orta", ""
        checks.append(Check("tls.cert", "TLS / HTTPS", "TLS sertifikası", st, detail, weight=15, severity=sev,
                            recommendation=rec, data={"days_left": days, "issuer": issuer_name, "version": res["version"]}))

    legacy = res.get("legacy", {})
    accepted = [k for k, v in legacy.items() if v is True]
    if accepted:
        checks.append(Check("tls.legacy", "TLS / HTTPS", "Eski TLS sürümleri", "warn",
                            f"Sunucu eski/güvensiz protokolleri kabul ediyor: {', '.join(accepted)}",
                            weight=5, severity="orta", recommendation="TLS 1.0 ve 1.1'i devre dışı bırakın."))
    elif legacy and all(v is None for v in legacy.values()):
        checks.append(Check("tls.legacy", "TLS / HTTPS", "Eski TLS sürümleri", "info",
                            "Bu sistemin OpenSSL'i TLS 1.0/1.1 testine izin vermiyor; test edilemedi.", weight=0))
    else:
        checks.append(Check("tls.legacy", "TLS / HTTPS", "Eski TLS sürümleri", "pass",
                            "TLS 1.0/1.1 kabul edilmiyor.", weight=5))
    return checks


# --------------------------------------------------------------------------- HTTP
SEC_HEADERS = [
    ("strict-transport-security", "HSTS", 6, "orta",
     "'Strict-Transport-Security: max-age=31536000; includeSubDomains' başlığını ekleyin."),
    ("content-security-policy", "Content-Security-Policy", 4, "düşük",
     "XSS riskini azaltmak için bir Content-Security-Policy tanımlayın."),
    ("x-content-type-options", "X-Content-Type-Options", 2, "düşük",
     "'X-Content-Type-Options: nosniff' başlığını ekleyin."),
    ("x-frame-options", "Clickjacking koruması (X-Frame-Options / frame-ancestors)", 3, "düşük",
     "'X-Frame-Options: DENY' veya CSP 'frame-ancestors' kullanın."),
    ("referrer-policy", "Referrer-Policy", 1, "düşük",
     "'Referrer-Policy: strict-origin-when-cross-origin' başlığını ekleyin."),
]


async def check_http(host: str, client: httpx.AsyncClient) -> list[Check]:
    checks: list[Check] = []
    # HTTP -> HTTPS yönlendirmesi
    try:
        r = await client.get(f"http://{host}/", follow_redirects=True)
        final = str(r.url)
        st = "pass" if final.startswith("https://") else "fail"
        checks.append(Check("http.redirect", "TLS / HTTPS", "HTTP → HTTPS yönlendirmesi", st,
                            f"http://{host}/ → {final}", weight=6, severity="orta",
                            recommendation="" if st == "pass" else "Tüm HTTP isteklerini HTTPS'e kalıcı (301) yönlendirin."))
    except httpx.HTTPError as e:
        checks.append(Check("http.redirect", "TLS / HTTPS", "HTTP → HTTPS yönlendirmesi", "info",
                            f"HTTP (80) yanıt vermedi: {type(e).__name__}", weight=0))

    try:
        r = await client.get(f"https://{host}/", follow_redirects=True)
    except httpx.HTTPError as e:
        checks.append(Check("http.headers", "Web Güvenliği", "HTTPS ana sayfa", "error",
                            f"HTTPS isteği başarısız: {type(e).__name__}: {e}", weight=0))
        return checks

    h = r.headers
    csp = h.get("content-security-policy", "")
    for key, title, w, sev, rec in SEC_HEADERS:
        present = key in h or (key == "x-frame-options" and "frame-ancestors" in csp.lower())
        if key == "strict-transport-security" and present:
            m = re.search(r"max-age=(\d+)", h[key])
            if not m or int(m.group(1)) < 15552000:
                checks.append(Check("http.hsts", "Web Güvenliği", title, "warn",
                                    f"HSTS süresi kısa: {h[key]}", weight=w, severity="düşük",
                                    recommendation="HSTS max-age değerini en az 6 ay (15552000) yapın."))
                continue
        checks.append(Check(f"http.{key}", "Web Güvenliği", title, "pass" if present else "fail",
                            h.get(key, "frame-ancestors (CSP)" if present else "Başlık yok"),
                            weight=w, severity=sev, recommendation="" if present else rec))

    leak = [f"{k}: {h[k]}" for k in ("server", "x-powered-by", "x-aspnet-version") if k in h and re.search(r"\d", h[k])]
    checks.append(Check("http.banner", "Web Güvenliği", "Yazılım sürümü ifşası", "warn" if leak else "pass",
                        "; ".join(leak) if leak else "Yanıt başlıklarında sürüm bilgisi yok.",
                        weight=2, severity="düşük",
                        recommendation="Server / X-Powered-By başlıklarından sürüm bilgisini kaldırın." if leak else ""))

    # security.txt (RFC 9116)
    try:
        s = await client.get(f"https://{host}/.well-known/security.txt", follow_redirects=True)
        ok = s.status_code == 200 and "contact:" in s.text.lower()
    except httpx.HTTPError:
        ok = False
    checks.append(Check("http.securitytxt", "Web Güvenliği", "security.txt (RFC 9116)", "pass" if ok else "warn",
                        "Güvenlik bildirim iletişimi yayınlanmış." if ok else "/.well-known/security.txt bulunamadı.",
                        weight=1, severity="düşük",
                        recommendation="" if ok else "Zafiyet bildirimleri için security.txt yayınlayın."))
    return checks


# --------------------------------------------------------------------------- OSINT API'leri
async def check_ct_subdomains(domain: str, client: httpx.AsyncClient) -> list[Check]:
    try:
        r = await client.get("https://crt.sh/", params={"q": f"%.{domain}", "output": "json"}, timeout=25)
        r.raise_for_status()
        names = set()
        for e in r.json():
            for n in str(e.get("name_value", "")).split("\n"):
                n = n.strip().lower().lstrip("*.")
                if n.endswith(domain):
                    names.add(n)
    except (httpx.HTTPError, ValueError) as e:
        return [Check("osint.subdomains", "Saldırı Yüzeyi", "Alt alan adları (CT kayıtları)", "error",
                      f"crt.sh sorgulanamadı: {type(e).__name__}", weight=0)]
    names = sorted(names)
    sensitive = [n for n in names if re.search(r"(^|[.-])(dev|test|staging|stage|uat|admin|vpn|jenkins|gitlab|jira|backup|old|beta)([.-]|$)", n)]
    detail = f"{len(names)} alt alan adı bulundu."
    if sensitive:
        detail += f" Dikkat çeken: {', '.join(sensitive[:10])}{' …' if len(sensitive) > 10 else ''}"
    return [Check("osint.subdomains", "Saldırı Yüzeyi", "Alt alan adları (CT kayıtları)", "info", detail,
                  weight=0, data={"subdomains": names[:500], "sensitive": sensitive})]


async def check_internetdb(ips: list[str], mx_ips: set[str], client: httpx.AsyncClient) -> list[Check]:
    pub = [ip for ip in dict.fromkeys(ips) if ipaddress.ip_address(ip).is_global][:5]
    if not pub:
        return []

    async def one(ip):
        try:
            r = await client.get(f"https://internetdb.shodan.io/{ip}")
            if r.status_code == 404:
                return ip, {"ports": [], "vulns": []}
            r.raise_for_status()
            return ip, r.json()
        except (httpx.HTTPError, ValueError) as e:
            return ip, {"error": type(e).__name__}

    results = dict(await asyncio.gather(*(one(ip) for ip in pub)))
    if all("error" in v for v in results.values()):
        return [Check("osint.ports", "Saldırı Yüzeyi", "Açık portlar (Shodan InternetDB)", "error",
                      "InternetDB sorgulanamadı.", weight=0)]

    checks = []
    risky, vulns, port_lines = [], set(), []
    for ip, d in results.items():
        ports = d.get("ports", [])
        port_lines.append(f"{ip}: {', '.join(map(str, ports)) or '-'}")
        for p in ports:
            if p in RISKY_PORTS and not (p == 25 and ip in mx_ips):
                risky.append(f"{ip}:{p} ({RISKY_PORTS[p]})")
        vulns.update(d.get("vulns", []))

    checks.append(Check("osint.ports", "Saldırı Yüzeyi", "İnternete açık riskli servisler",
                        "fail" if risky else "pass",
                        ("Riskli servisler: " + ", ".join(risky)) if risky else "Bilinen riskli port yok. " + " | ".join(port_lines),
                        weight=10, severity="yüksek",
                        recommendation="Veritabanı, RDP, SMB vb. servisleri internete kapatın; VPN/IP kısıtı arkasına alın." if risky else "",
                        data={"hosts": results}))
    vl = sorted(vulns)
    checks.append(Check("osint.cves", "Saldırı Yüzeyi", "Bilinen zafiyetler (CVE)",
                        "fail" if vl else "pass",
                        (f"{len(vl)} CVE ile ilişkilendirilmiş: " + ", ".join(vl[:15]) + (" …" if len(vl) > 15 else "")) if vl
                        else "Shodan verisinde bilinen CVE yok.",
                        weight=15, severity="kritik" if len(vl) >= 5 else "yüksek",
                        recommendation="Tespit edilen yazılımları güncelleyin; CVE'lerin geçerliliğini tedarikçiyle doğrulayın." if vl else "",
                        data={"cves": vl}))
    return checks


async def check_rdap(domain: str, client: httpx.AsyncClient) -> list[Check]:
    try:
        r = await client.get(f"https://rdap.org/domain/{domain}", follow_redirects=True)
        r.raise_for_status()
        j = r.json()
    except (httpx.HTTPError, ValueError) as e:
        return [Check("osint.rdap", "Domain Kaydı", "Domain kayıt bilgisi", "info",
                      f"RDAP bilgisi alınamadı ({type(e).__name__}); .tr gibi bazı uzantılar RDAP desteklemez.", weight=0)]
    ev = {e.get("eventAction"): e.get("eventDate", "")[:10] for e in j.get("events", [])}
    registrar = next((
        next((v[3] for v in (ent.get("vcardArray") or [None, []])[1] if v[0] == "fn"), None)
        for ent in j.get("entities", []) if "registrar" in ent.get("roles", [])), None)
    exp = ev.get("expiration")
    created = ev.get("registration")
    detail = f"Kayıt: {created or '?'} | Bitiş: {exp or '?'} | Kayıt firması: {registrar or '?'}"
    st, rec = "info", ""
    w = 0
    if exp:
        days = (dt.date.fromisoformat(exp) - dt.date.today()).days
        w = 5
        if days < 30:
            st, rec = "fail", "Domain süresi 30 günden az; yenilenmezse hizmet kesintisi ve domain kaybı riski var."
        else:
            st = "pass"
    if created and (dt.date.today() - dt.date.fromisoformat(created)).days < 180:
        detail += " | DİKKAT: domain 6 aydan yeni."
    return [Check("osint.rdap", "Domain Kaydı", "Domain kayıt bilgisi", st, detail, weight=w, severity="yüksek",
                  recommendation=rec, data={"created": created, "expires": exp, "registrar": registrar})]


# --------------------------------------------------------------------------- Ana fonksiyon
def score(checks: list[Check]) -> tuple[float | None, str | None]:
    scored = [c for c in checks if c.status in STATUS_POINTS and c.weight > 0]
    total = sum(c.weight for c in scored)
    if not total:
        return None, None
    s = round(100 * sum(c.weight * STATUS_POINTS[c.status] for c in scored) / total, 1)
    grade = "A" if s >= 90 else "B" if s >= 80 else "C" if s >= 70 else "D" if s >= 60 else "F"
    return s, grade


async def scan_domain(domain: str) -> dict:
    domain = normalize_domain(domain)
    started = dt.datetime.now(dt.timezone.utc)
    async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": UA}) as client:
        resolver = Resolver(client)
        dns_checks, info = await check_dns(domain, resolver)

        # Web sitesi www altında olabilir
        web_host = domain
        if not info["a"] and not info["aaaa"]:
            try:
                www = await resolver.query(f"www.{domain}", "A")
            except Exception:
                www = []
            if www:
                web_host = f"www.{domain}"
                info["a"] = [x for x in www if re.match(r"^\d+\.\d+\.\d+\.\d+$", x)]

        mx_ips: list[str] = []
        for mxh in info["mx"][:3]:
            try:
                mx_ips += await resolver.query(mxh, "A")
            except Exception:
                pass
        ips = [x for x in info["a"] + mx_ips if re.match(r"^\d+\.\d+\.\d+\.\d+$", x)]

        groups = await asyncio.gather(
            check_tls(web_host),
            check_http(web_host, client),
            check_ct_subdomains(domain, client),
            check_internetdb(ips, set(mx_ips), client),
            check_rdap(domain, client),
            return_exceptions=True,
        )
    checks = list(dns_checks)
    names = ["TLS", "HTTP", "CT", "InternetDB", "RDAP"]
    for n, g in zip(names, groups):
        if isinstance(g, Exception):
            checks.append(Check(f"error.{n.lower()}", "Sistem", f"{n} kontrolü", "error",
                                f"{type(g).__name__}: {g}", weight=0))
        else:
            checks.extend(g)

    s, grade = score(checks)
    return {
        "domain": domain,
        "web_host": web_host,
        "scanned_at": started.isoformat(timespec="seconds"),
        "duration_s": round((dt.datetime.now(dt.timezone.utc) - started).total_seconds(), 1),
        "score": s,
        "grade": grade,
        "summary": {st: sum(1 for c in checks if c.status == st) for st in ("pass", "warn", "fail", "info", "error")},
        "checks": [asdict(c) for c in checks],
    }


if __name__ == "__main__":  # python -m app.intel.scanner ornek.com
    import json
    import sys
    print(json.dumps(asyncio.run(scan_domain(sys.argv[1])), ensure_ascii=False, indent=2))

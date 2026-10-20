"""Configuration classes: the part of a session's setup that decides whether a fault shows.

A class is server filesystem, MDT layout, client platform, security and network.
Distro versions are not part of it: the numbered review groups rotate through them
over time.  Each class has the Test-Parameters keys that ask Autotest for it; the
predictor writes those, and the grader maps every session it ran to a class.
"""
from __future__ import annotations

import re

# name: (Test-Parameters keys, verified, what it is).  verified: custom sessions with
# exactly these keys ran in this class (eval/check_classes.py).  keys None: no line
# asks for it alone; the predictor can only ask for the whole group, at its cost.
CLASSES: dict[str, tuple[str | None, bool, str]] = {
	"ldiskfs-dne": ("", True, "ldiskfs, 2 MDS with 4 MDTs, 8 OSTs: a custom session's default "
			"(review-dne-part-N, review-ldiskfs-dne)"),
	"zfs-dne": ("fstype=zfs", True, "ZFS, 2 MDS with 4 MDTs (review-dne-zfs-part-N)"),
	"ldiskfs-dne-arm": ("clientarch=aarch64 clientdistro=rocky9.8", True,
			    "aarch64 clients, DNE (review-ldiskfs-dne-arm)"),
	"ldiskfs": ("mdscount=1 mdtcount=1 ostcount=7", False, "ldiskfs, one MDT, 7 OSTs (review-ldiskfs)"),
	"zfs": ("fstype=zfs mdscount=1 mdtcount=1 ostcount=2", False, "ZFS, one MDT, 2 OSTs (review-zfs)"),
	"ldiskfs-arm": ("clientarch=aarch64 clientdistro=rocky9.8 mdscount=1 mdtcount=1 ostcount=7", False,
			"aarch64 clients, one MDT (review-ldiskfs-arm)"),
	"ldiskfs-ubuntu": ("clientdistro=ubuntu2404 mdscount=1 mdtcount=1 ostcount=7", False,
			   "Ubuntu clients (review-ldiskfs-ubuntu)"),
	"ldiskfs-sles": ("clientdistro=sles15sp5 mdscount=1 mdtcount=1 ostcount=7", False,
			 "SLES clients (review-ldiskfs on SLES)"),
	"ldiskfs-dne-selinux-ssk": (None, False, "SELinux and shared-secret keys, DNE "
				    "(review-dne-selinux-ssk-part-N): testgroup= only, which runs the group's suites"),
	"ldiskfs-dne-ipv6": (None, False, "IPv6, DNE (review-dne-ipv6): testgroup= only"),
}

# Scripts that need keys of their own in any class: sanity-krb5 skips without Kerberos.
SCRIPT_KEYS: dict[str, str] = {"sanity-krb5": "kerberos=true"}

_NAME = re.compile(r"lustre-[a-z0-9.-]+?-(?P<distro>el[0-9.]+|rocky[0-9.]+|sles[0-9a-z.]+|ubuntu[0-9.]+|"
		   r"centos[0-9.]+|oel[0-9.]+)-(?P<arch>x86_64|aarch64)")


def client_platform(test_name: str | None, client_distro: str | None) -> tuple[str, str]:
	"""(distro family, arch) of the clients: the last build named in Autotest's
	session name is the clients' (an interop session names the servers' first)."""
	found = list(_NAME.finditer(test_name or ""))
	if found:
		d, arch = found[-1].group("distro"), found[-1].group("arch")
	else:
		d, arch = (client_distro or "").lower().replace(" ", ""), ""
	fam = ("ubuntu" if d.startswith("ubuntu") else "sles" if d.startswith("sles") else "el")
	return fam, arch or "x86_64"


def class_of(s: dict) -> str | None:
	"""The class of one session (a dict of the dataset's session columns), or None
	for one whose layout says it never got going."""
	group = s.get("test_group") or ""
	fs = s.get("server_fs") or ("zfs" if "zfs" in group else "ldiskfs")
	fam, arch = client_platform(s.get("test_name"), s.get("client_distro"))
	if s.get("mdts"):
		dne = s["mdts"] > 1
	elif group == "custom":
		return None
	else:
		dne = "dne" in group
	if s.get("mdts") and not s.get("osts") and group == "custom":
		return None
	base = f"{fs}{'-dne' if dne else ''}"
	if "selinux" in group or "ssk" in group:
		return f"{base}-selinux-ssk" if base == "ldiskfs-dne" else None
	if "ipv6" in group:
		return f"{base}-ipv6" if base == "ldiskfs-dne" else None
	if arch == "aarch64":
		return f"{base}-arm" if fs == "ldiskfs" else None
	if fam in ("ubuntu", "sles"):
		return f"ldiskfs-{fam}" if base == "ldiskfs" else None
	return base

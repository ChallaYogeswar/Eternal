"""Server-side authentication, caller identity and approval challenges for every Eternal HTTP daemon (audit E-01).

Before this package the APIs trusted the request body: a caller could claim `confirmation_channel=text_typed`,
`caller_kind=owner_interactive` or `granted_by=app_tap`, and /consent/grant, /tools/grant and /vault/tokens had no
authentication at all. Now:

  * every state-changing route needs a bearer token that maps to a configured PRINCIPAL (`principals.py`);
  * the confirmation channel and caller kind come from the principal, never from the body (`derive_call_identity`);
  * SENSITIVE tool calls need a separate, human-confirmed, one-time approval bound to the exact tool, arguments,
    session and requester (`approvals.py`);
  * with no principals configured the daemons refuse everything protected (fail closed).

Pure logic only: this package imports no phase package, so any daemon can depend on it.
"""

# DNS Setup Options

Claude Code's `/login` requires the gateway hostname to resolve **only** to private
IPs. This stack publishes the gateway A-record in a **Route 53 Private Hosted
Zone**, which resolves only inside the VPC. The developer machine must therefore
reach that private zone. Pick the option that matches how developers connect.

## Option A: Client VPN uses VPC DNS

Use this when AWS Client VPN connects directly to the gateway VPC.

```text
Developer laptop
  -> Client VPN DNS setting
  -> VPC resolver
  -> Route 53 Private Hosted Zone
  -> internal ALB alias
```

Set the Client VPN endpoint DNS server to the VPC resolver address, for example
`10.0.0.2` when the VPC CIDR is `10.0.0.0/16`. This is the default assumed by the
[Client VPN walkthrough](client-vpn.md).

## Option B: Corporate DNS forwards to Route 53 Resolver

Use this when developers already use corporate DNS over VPN, Direct Connect, or
ZTNA.

```text
Developer laptop
  -> corporate DNS
  -> conditional forwarder for corp.example.com
  -> Route 53 Resolver inbound endpoint
  -> Route 53 Private Hosted Zone
```

Create a Route 53 Resolver inbound endpoint in the VPC, then configure corporate
DNS to forward `corp.example.com` to the inbound endpoint IP addresses. The inbound
endpoint IPs are private, so the corporate network must already be connected to the
VPC through VPN or Direct Connect.

AWS reference:
[Forwarding inbound DNS queries to your VPCs](https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/resolver-forwarding-inbound-queries.html)

## Option C: Devbox inside the VPC

Use this when you do not want to configure laptop VPN/DNS immediately.

```text
Developer SSH session / remote devbox
  -> VPC DNS
  -> Route 53 Private Hosted Zone
  -> internal ALB
```

Run Claude Code from an EC2/WorkSpaces/devbox environment in the VPC. The machine
should use the VPC resolver and have security-group/network access to the internal
ALB.

## Note on corporate proxies

If developer machines route HTTPS through a corporate proxy, the proxy host must
also resolve to private addresses; otherwise add the gateway host to `NO_PROXY` so
the CLI connects directly.

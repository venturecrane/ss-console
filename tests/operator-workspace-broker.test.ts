import { describe, expect, it } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

const dockerfile = readFileSync(resolve('operator/templates/Dockerfile'), 'utf8')
const entrypoint = readFileSync(resolve('operator/templates/entrypoint.sh'), 'utf8')
const bootstrap = readFileSync(resolve('operator/templates/bootstrap.sh'), 'utf8')
const bootSmoke = readFileSync(resolve('operator/bin/boot-smoke-test.sh'), 'utf8')
const workspaceSkill = readFileSync(resolve('operator/skills/workspace/SKILL.md'), 'utf8')
const inboxTriageSkill = readFileSync(resolve('operator/skills/inbox-triage/SKILL.md'), 'utf8')
const emailReplySkill = readFileSync(resolve('operator/skills/email-reply/SKILL.md'), 'utf8')
const smdCustomerConfig = readFileSync(
  resolve('operator/customers/smd-staging/customer.yaml'),
  'utf8'
)

describe('ADR 0045 Workspace capability broker', () => {
  it('runs broker and gateway under distinct non-root principals', () => {
    expect(dockerfile).toContain('useradd -u 10000 -m -d /opt/data hermes')
    expect(dockerfile).toContain('useradd -u 10001 -r -m -d /opt/workspace-broker workspace-broker')
    // ss#2614: the chronology runner's child uid. No home, no shell; its one
    // shared group is the Smokeball token file it mints against beside hermes.
    expect(dockerfile).toContain(
      'useradd -u 10002 -r -M -d /nonexistent -s /usr/sbin/nologin medchron'
    )
    expect(dockerfile).toContain('usermod -a -G smokeball-token medchron')
    expect(dockerfile).toContain('usermod -a -G smokeball-token hermes')
    expect(entrypoint).toContain('--reuid=workspace-broker')
    expect(entrypoint).toContain('--reuid=hermes')
    expect(entrypoint).toContain('--no-new-privs')
    expect(entrypoint).toContain('/usr/bin/env -i')
    expect(entrypoint).toContain('PYTHONPATH="/opt/workspace-broker"')
    expect(dockerfile).not.toMatch(/\bsudo\b/)
  })

  it('keeps broker code root-owned and credentials broker-only', () => {
    expect(dockerfile).toContain('chown -R root:root /opt/workspace-broker')
    expect(entrypoint).toContain('chown -R hermes:hermes /opt/data')
    expect(entrypoint).toContain('BROKER_DIR="/var/lib/smd-workspace-broker"')
    expect(entrypoint).not.toContain('BROKER_DIR="/opt/data/workspace-broker"')
    expect(entrypoint).toContain('rm -rf /opt/data/workspace-broker')
    expect(entrypoint).toContain('rm -f /opt/data/oauth/google.json')
    expect(entrypoint).toContain('rm -f "${BROKER_DIR}/google.json"')
    // Keystone (#1407): the broker's customer.yaml is now copied from the
    // root-owned LIVE_CUSTOMER_YAML (R2 source of truth), not the agent-writable
    // /opt/data — that relocation IS the self-loopback fix.
    expect(entrypoint).toContain('cp "${LIVE_CUSTOMER_YAML}" "${BROKER_CUSTOMER_PATH}"')
    expect(entrypoint).toContain('chmod 0700 "${BROKER_DIR}"')
    expect(entrypoint).toContain('chown -R workspace-broker:workspace-broker "${BROKER_DIR}"')
    expect(entrypoint).toContain(
      'chown workspace-broker:workspace-broker "${SMD_WORKSPACE_CREDENTIAL_PATH}"'
    )
    expect(entrypoint).toContain('chmod 0600 "${SMD_WORKSPACE_CREDENTIAL_PATH}"')
    expect(entrypoint).toContain(
      'materialize_credential(Path(os.environ["SMD_WORKSPACE_CREDENTIAL_PATH"]))'
    )
    expect(entrypoint.indexOf('chown -R hermes:hermes /opt/data')).toBeLessThan(
      entrypoint.indexOf('chown -R workspace-broker:workspace-broker "${BROKER_DIR}"')
    )
  })

  it('runs the gateway with a writable non-root home', () => {
    expect(entrypoint).toContain('export HOME=/opt/data')
    expect(entrypoint.indexOf('export HOME=/opt/data')).toBeLessThan(
      entrypoint.lastIndexOf('exec setpriv')
    )
    expect(entrypoint).not.toContain('HERMES_HOME_MODE')
  })

  it('strips every Google credential from the gateway environment', () => {
    expect(entrypoint).toContain(
      'unset GOOGLE_SERVICE_ACCOUNT_JSON GOOGLE_TOKEN_JSON GOOGLE_CLIENT_SECRET_JSON'
    )
    expect(entrypoint).toContain(
      'unset GOOGLE_IMPERSONATE_SUBJECT GOOGLE_OAUTH_SCOPES GOOGLE_TOKEN_PATH'
    )
    expect(bootstrap).not.toContain('GOOGLE_TOKEN_FILE="/opt/data/oauth/google.json"')
  })

  it('does not pass Google credentials into the broker child environment', () => {
    const brokerChild = entrypoint.slice(entrypoint.indexOf('setpriv'))
    expect(brokerChild).not.toContain('GOOGLE_SERVICE_ACCOUNT_JSON=')
    expect(brokerChild).not.toContain('GOOGLE_TOKEN_JSON=')
  })

  // ss#2258. The AgentMail SEND key gets the same custody as the Google one:
  // materialized to a 0600 broker-owned file, passed to the broker as a PATH,
  // and unset before the gateway exists. The gateway keeps AGENTMAIL_API_KEY —
  // a different, inbox-scoped key the vendor refuses to let transmit — so the
  // two names must never be conflated.
  it('strips the AgentMail send credential from the gateway environment', () => {
    expect(entrypoint).toContain('unset AGENTMAIL_SEND_API_KEY')
    // Order is the whole control: ADR 0044 D8 showed a same-uid sibling can read
    // a credential out of /proc/<pid>/environ, so a strip after the exec-drop
    // would be cosmetic.
    expect(entrypoint.indexOf('unset AGENTMAIL_SEND_API_KEY')).toBeLessThan(
      entrypoint.lastIndexOf('exec setpriv')
    )
  })

  it('gives the broker the AgentMail send credential by path, never by value', () => {
    const brokerChild = entrypoint.slice(entrypoint.indexOf('setpriv'))
    expect(brokerChild).toContain('SMD_AGENTMAIL_CREDENTIAL_PATH=')
    // The secret itself must not ride the broker child env: the broker reads the
    // file, so a respawn needs nothing the parent later unset.
    expect(brokerChild).not.toContain('AGENTMAIL_SEND_API_KEY=')
    expect(brokerChild).not.toContain('AGENTMAIL_API_KEY=')
  })

  it('locks the AgentMail send credential to the broker uid at 0600', () => {
    expect(entrypoint).toContain(
      'chown workspace-broker:workspace-broker "${SMD_AGENTMAIL_CREDENTIAL_PATH}"'
    )
    expect(entrypoint).toContain('chmod 0600 "${SMD_AGENTMAIL_CREDENTIAL_PATH}"')
  })

  it('proves the AgentMail send-key strip on the RUNNING machine, not just in source', () => {
    // The incident class is invisible to static tests — what matters is what is
    // in a live process's environ. Reuses the R2 probe by argument.
    expect(bootSmoke).toContain('agentmail-send-key-stripped-from-agent')
    expect(bootSmoke).toContain('r2-account-key-strip-probe.py hermes AGENTMAIL_SEND_API_KEY')
  })

  // ss#2258 msgraph wave. Same custody shape for the Graph SEND app credential —
  // and one deliberate asymmetry that these tests pin so it cannot be "tidied"
  // into symmetry by someone who reads the block above and assumes an omission.
  it('strips the Graph send credential from the gateway environment', () => {
    expect(entrypoint).toContain('unset MSGRAPH_SEND_TENANT_ID')
    expect(entrypoint).toContain('MSGRAPH_SEND_CLIENT_SECRET')
    expect(entrypoint.indexOf('unset MSGRAPH_SEND_TENANT_ID')).toBeLessThan(
      entrypoint.lastIndexOf('exec setpriv')
    )
  })

  it('KEEPS the Graph READ credential in the gateway, on purpose', () => {
    // The agent needs MSGRAPH_CLIENT_SECRET for the inbound delta poller and the
    // msgraph-mail MCP server, and Graph app-only auth has no read-only variant
    // of a send-capable credential (a client-credentials token is always
    // `/.default`). Stripping it would blind the seat, not harden it. Deleting
    // this test to "fix the inconsistency" is the mistake it guards against.
    const stripSection = entrypoint.slice(entrypoint.indexOf('unset AGENTMAIL_SEND_API_KEY'))
    expect(stripSection).not.toMatch(/unset[^\n]*\bMSGRAPH_CLIENT_SECRET\b/)
    expect(stripSection).not.toMatch(/unset[^\n]*\bMSGRAPH_MAILBOX\b/)
  })

  it('gives the broker the Graph send credential by path, never by value', () => {
    const brokerChild = entrypoint.slice(entrypoint.indexOf('setpriv'))
    expect(brokerChild).toContain('SMD_MSGRAPH_CREDENTIAL_PATH=')
    expect(brokerChild).not.toContain('MSGRAPH_SEND_CLIENT_SECRET=')
    // Nor the read credential: the broker has no business reading mail, and the
    // mailbox it sends as comes from customer.yaml rather than from a secret.
    expect(brokerChild).not.toContain('MSGRAPH_CLIENT_SECRET=')
    expect(brokerChild).not.toContain('MSGRAPH_MAILBOX=')
  })

  it('locks the Graph send credential to the broker uid at 0600', () => {
    expect(entrypoint).toContain(
      'chown workspace-broker:workspace-broker "${SMD_MSGRAPH_CREDENTIAL_PATH}"'
    )
    expect(entrypoint).toContain('chmod 0600 "${SMD_MSGRAPH_CREDENTIAL_PATH}"')
  })

  it('proves the Graph send-credential strip on the RUNNING machine', () => {
    expect(bootSmoke).toContain('msgraph-send-credential-stripped-from-agent')
    expect(bootSmoke).toContain('hermes MSGRAPH_SEND_CLIENT_SECRET')
  })

  it('does not install Google provider libraries into the Hermes venv', () => {
    expect(dockerfile).not.toMatch(
      /uv pip install --no-cache-dir google-api-python-client google-auth/
    )
    expect(dockerfile).toContain('uv pip install --python /opt/workspace-broker/.venv/bin/python')
  })

  it('migrates the Workspace skill away from direct credential paths', () => {
    expect(workspaceSkill).toContain('workspace_docs_create')
    expect(workspaceSkill).not.toContain('/opt/data/oauth/google.json')
    expect(workspaceSkill).not.toContain('/app/connectors/google/')
  })

  it('keeps Wave A Gmail skills on read and draft tools only', () => {
    expect(inboxTriageSkill).toContain('workspace_gmail_search')
    expect(inboxTriageSkill).toContain('workspace_gmail_create_draft')
    expect(emailReplySkill).toContain('workspace_gmail_create_draft')
    expect(inboxTriageSkill).not.toContain('workspace_gmail_send')
    expect(emailReplySkill).not.toContain('workspace_gmail_send')
    expect(inboxTriageSkill).not.toContain('send_message')
    expect(smdCustomerConfig).not.toContain('workspace_gmail_send')
    expect(smdCustomerConfig).not.toContain('receive an autonomous reply')
  })
})

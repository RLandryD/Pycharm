"""scaffolder/error_handling.py — gold-standard exception subprocess injection.

Decoded from SAP's own published reference, the *Integration Flow Design
Guidelines — Handle Errors Gracefully* package (reviewed 2026-06-10 alongside
QforIT Error Alerting and the SAP Alert Notification service integrations).
The Guidelines establish three canonical exception-subprocess shapes:

  error_end       Error Start → Script (enrich MPL) → **Error End**
                  MPL stays FAILED; the sender still receives the error.
                  The Guidelines' baseline — honest status + full context.
  escalation_end  Error Start → Script → **Escalation End**
                  MPL status ESCALATED (visible triage queue in Monitor).
  message_end     Error Start → Script → Content Modifier (error body,
                  SAP_MessageProcessingLogCustomStatus, CamelHttpResponseCode)
                  → **Message End** — "do not throw error on failure": sync
                  callers get a crafted error response, MPL COMPLETED with a
                  custom status.

All event/step ifl:property sets here are VERBATIM from the Guidelines
bundles (ErrorEndEvent / EscalationEndEvent[ErrorCode=Others] /
ErrorStartEvent / Enricher 1.6). The capture script generalizes the
Guidelines' demo scripts to production grade: exception class+message+bounded
stack trace, MPL ID, secret-redacted headers, property names, failed payload
attachment, and an MPL custom status — everything the side-car monitors
(Alert Notification service flow) key on later.

The injector NEVER touches flows that already carry an exception subprocess
(fidelity first); it only extends flows that have none.
"""
from __future__ import annotations

def gold_capture_script(variant: str = "error_end") -> str:
    """Variant-aware capture: the custom status PREFIX names the policy the
    flow applies (Failed_/Escalated_/Handled_ + exception class), so Monitor
    triage can tell at a glance which handling each failed message got —
    user feedback: identical scripts made Error End and Escalation End look
    functionally identical even though the END EVENT drives the MPL status
    (FAILED vs ESCALATED) and sender behavior."""
    prefix = {"error_end": "Failed_", "escalation_end": "Escalated_",
              "message_end": "Handled_"}.get(variant, "Failed_")
    policy = {"error_end": "Error End (MPL FAILED, sender notified)",
              "escalation_end": "Escalation End (MPL ESCALATED)",
              "message_end":
                  "Message End (MPL COMPLETED, crafted error response)",
              }.get(variant, "Error End")
    return (GOLD_CAPTURE_SCRIPT
            .replace("'Failed_' +", f"'{prefix}' +")
            .replace("HANDLING_POLICY", policy))


GOLD_CAPTURE_SCRIPT = r"""import com.sap.gateway.ip.core.customdev.util.Message

/* Gold-standard error capture (pattern: SAP Integration Flow Design
   Guidelines - Handle Errors Gracefully). Attaches full error context to the
   MPL and sets a custom status so monitoring flows and the Monitor UI can
   triage without opening traces. */
def Message processData(Message message) {
    def log = messageLogFactory.getMessageLog(message)
    def ex = message.getProperty('CamelExceptionCaught')
    def mplId = message.getProperty('SAP_MessageProcessingLogID') ?: 'n/a'

    // classify the error so subjects/bodies can say WHY (best-effort
    // heuristic on class+message; refine per client as patterns emerge)
    def blob = ((exClass ?: '') + ' ' + (exMsg ?: ''))
    def reason = 'Execution error'
    if (blob =~ /(?i)(connect|timed? ?out|unknownhost|no route|refused|ssl|handshake|certificat|50[234])/)
        reason = 'Endpoint error'
    else if (blob =~ /(?i)(saxpars|xml.*pars|unmarshal|jsonpars|validat|schema|malformed|typeconversion)/)
        reason = 'Incoming message error'
    else if (blob =~ /(?i)(xslt|mapping|transform)/)
        reason = 'Mapping error'
    else if (blob =~ /(?i)(status code [45]|odata|http operation failed|40[013478])/)
        reason = 'Outgoing message error'
    message.setProperty('ALERT_REASON', reason)
    message.setProperty('DateTime',
        new Date().format('yyyy-MM-dd HH:mm:ss', TimeZone.getTimeZone('UTC')))

    def sb = new StringBuilder()
    sb.append('Handling policy: HANDLING_POLICY\n')
    sb.append('MPL ID: ').append(mplId).append('\n')
    if (ex != null) {
        sb.append('Exception class: ')
          .append(ex.getClass().getCanonicalName()).append('\n')
        sb.append('Exception message: ')
          .append(ex.getMessage() ?: '').append('\n')
        def st = ex.getStackTrace()
        if (st != null && st.length > 0) {
            int n = Math.min(st.length, 15)
            sb.append('Stack trace (first ').append(n).append('):\n')
            for (int i = 0; i < n; i++) {
                sb.append('  at ').append(st[i].toString()).append('\n')
            }
        }
    } else {
        sb.append('No CamelExceptionCaught present.\n')
    }
    sb.append('Headers at failure (secrets redacted):\n')
    message.getHeaders().each { k, v ->
        if (!(k ==~ /(?i).*(auth|password|token|secret|cookie|apikey).*/)) {
            sb.append('  ').append(k).append(' = ')
              .append(String.valueOf(v).take(200)).append('\n')
        }
    }
    sb.append('Exchange property names:\n')
    message.getProperties().keySet().sort().each { k ->
        sb.append('  ').append(k).append('\n')
    }

    if (log != null) {
        log.addAttachmentAsString('ErrorContext', sb.toString(), 'text/plain')
        def body = ''
        try { body = (message.getBody(java.lang.String) ?: '') }
        catch (Exception ignore) { }
        if (body) {
            log.addAttachmentAsString('FailedPayload',
                body.take(100000), 'text/plain')
        }
        log.setStringProperty('ErrorType',
            ex != null ? ex.getClass().getSimpleName() : 'Unknown')
    }
    // visible in Monitor's Custom Status column (alphanumeric + _, max 40)
    def cs = 'Failed_' + (ex != null ? ex.getClass().getSimpleName()
                                      : 'Unknown')
    message.setProperty('SAP_MessageProcessingLogCustomStatus',
        cs.replaceAll('[^A-Za-z0-9_]', '_').take(40))
    return message
}
"""

SCRIPT_REL_PATH = "src/main/resources/script/gold_error_capture.groovy"

#: verbatim event/step configs (Design Guidelines bundles)
_ERR_START = {"activityType": "StartErrorEvent",
              "cmdVariantUri": "ctype::FlowstepVariant/cname::ErrorStartEvent"}
_ERR_END = {"activityType": "EndErrorEvent",
            "cmdVariantUri": "ctype::FlowstepVariant/cname::ErrorEndEvent"}
_ESC_END = {"ErrorCode": "Others", "componentVersion": "1.0",
            "activityType": "EscalationEndEvent",
            "cmdVariantUri":
                "ctype::FlowstepVariant/cname::EscalationEndEvent/"
                "version::1.0.0"}
_MSG_END = {"componentVersion": "1.1", "activityType": "EndEvent",
            "cmdVariantUri":
                "ctype::FlowstepVariant/cname::MessageEndEvent/version::1.1.0"}
_SUBPROC = {"componentVersion": "1.1",
            "activityType": "ErrorEventSubProcessTemplate",
            "cmdVariantUri": "ctype::FlowstepVariant/cname::"
                             "ErrorEventSubProcessTemplate/version::1.1.0"}
_GS = {"componentVersion": "1.1", "activityType": "Script",
       "cmdVariantUri":
           "ctype::FlowstepVariant/cname::GroovyScript/version::1.1.2",
       "subActivityType": "GroovyScript",
       "script": "gold_error_capture.groovy",
       "scriptFunction": "", "scriptBundleId": ""}
# message_end variant CM: Guidelines' 'Do Not Throw Error on Failure' shape,
# generalized (their wrapContent/custom status, with ${exception.message})
_CM_ERROR_RESPONSE = {
    "bodyType": "expression", "componentVersion": "1.6",
    "activityType": "Enricher",
    "cmdVariantUri": "ctype::FlowstepVariant/cname::Enricher/version::1.6.0",
    # body lives in wrapContent (bodyType=expression) — bodyContent is a
    # dead key CPI ignores (same bug class the user caught in the mail CM)
    "wrapContent": "<error><flow>${camelId}</flow>"
                   "<reason>${property.ALERT_REASON}</reason>"
                   "<message>${exception.message}</message></error>",
    "headerTable": "<row><cell id='Action'>Create</cell>"
                   "<cell id='Type'>constant</cell><cell id='Value'>500</cell>"
                   "<cell id='Default'></cell>"
                   "<cell id='Name'>CamelHttpResponseCode</cell>"
                   "<cell id='Datatype'></cell></row>",
}

VARIANTS = ("error_end", "escalation_end", "message_end")

# client naming preference: space | underscore | hyphen (set from the
# workbench; underscore is the long-standing default)
_WORD_SEP = "_"


def set_word_separator(sep: str) -> None:
    global _WORD_SEP
    _WORD_SEP = sep if sep in (" ", "_", "-") else "_"


def _n(label: str) -> str:
    """Apply the client's word-separator preference to a display name."""
    return label.replace(" ", _WORD_SEP)

_ROLE_TO_ALERT_INV = {}   # filled after _ROLE_TO_ALERT is defined (below)


def has_exception_subprocess(model) -> bool:
    return any(s.kind == "ErrorEventSubProcessTemplate"
               for s in model.steps.values())


def find_existing_alert_lip(model) -> str | None:
    """RCI093's pattern: every exception subprocess (main + LIPs) calls ONE
    central alert LIP. Detect it — ProcessCalls whose parent is an exception
    subprocess, targeting a non-main process — so injection REUSES the
    flow's own alert handler instead of adding a duplicate (user request:
    'we don't need additional error handlers that do the same')."""
    from collections import Counter
    subs = {s.id for s in model.steps.values()
            if s.kind == "ErrorEventSubProcessTemplate"}
    if not subs:
        return None
    lips = {p.id for p in model.processes if not p.is_main}
    counts = Counter()
    for s in model.steps.values():
        if s.config.get("activityType") == "ProcessCallElement" \
                and s.parent_subprocess in subs:
            pid = s.config.get("processId", "")
            if pid in lips:
                counts[pid] += 1
    return counts.most_common(1)[0][0] if counts else None


def _remove_main_exception_subprocesses(model, main) -> bool:
    """Remove every MAIN-process exception subprocess (and its transitive
    children, sequence flows, routes, message flows, and now-orphaned
    endpoint participants — 20 of 64 corpus subprocess flows wire mail
    alerts from inside the subprocess). LIP-level exception subprocesses are
    deliberately kept: they handle THAT process's errors, a different
    semantic scope. Returns True when something was removed."""
    subs = [s.id for s in model.steps.values()
            if s.kind == "ErrorEventSubProcessTemplate"
            and s.process_id == main.id and not s.parent_subprocess]
    if not subs:
        return False
    removed = set(subs)
    changed = True
    while changed:                                  # transitive children
        changed = False
        for s in model.steps.values():
            if s.id not in removed and s.parent_subprocess in removed:
                removed.add(s.id)
                changed = True
    ft = getattr(model, "_flow_target", None) or {}
    dead_fids = set()
    for sid in removed:
        st = model.steps[sid]
        dead_fids.update(st.outgoing)
        dead_fids.update(st.incoming)
    for fid in dead_fids:
        ft.pop(fid, None)
    if getattr(model, "routes", None):
        model.routes = [r for r in model.routes
                        if r.flow_id not in dead_fids]
    dead_mf = [mf for mf in model.message_flows
               if mf.source in removed or mf.target in removed]
    if dead_mf:
        keep = [mf for mf in model.message_flows if mf not in dead_mf]
        model.message_flows = keep
        live = {mf.source for mf in keep} | {mf.target for mf in keep}
        dead_parts = ({mf.source for mf in dead_mf}
                      | {mf.target for mf in dead_mf}) - removed
        model.endpoints = [e for e in model.endpoints
                           if e.id not in dead_parts or e.id in live]
    for sid in removed:
        del model.steps[sid]
    main.step_ids = [i for i in main.step_ids if i not in removed]
    if hasattr(model, "sequence") and isinstance(model.sequence, list):
        model.sequence = [i for i in model.sequence if i not in removed]
    return True


def inject_gold_error_handling(model, variant: str = "error_end",
                               replace_existing: bool = False,
                               notify_mail: bool = False,
                               notify_sftp: bool = False,
                               company: str = "company",
                               existing_params: str | None = None) -> dict:
    """Add the gold-standard exception subprocess. Default policy: only flows
    with NO main-process exception subprocess are touched (pure fidelity for
    the rest). With replace_existing=True the client chooses the gold pattern
    OVER their current one: existing main-process exception subprocesses are
    removed first (mail-alert wiring pruned cleanly), then the chosen variant
    is injected. Returns the resource files to ship ({rel_path: content}),
    empty dict when nothing was injected."""
    from extractor.iflow_parser import Step

    if variant not in VARIANTS:
        variant = "error_end"
    main = next((p for p in model.processes if p.is_main), None)
    if main is None:
        return {}
    has_main_sub = any(
        s.kind == "ErrorEventSubProcessTemplate"
        and s.process_id == main.id and not s.parent_subprocess
        for s in model.steps.values())
    # detect the flow's own central alert LIP BEFORE any removal (the main
    # subprocess's ProcessCall is removed under replace, but the called LIP
    # survives — and should be REUSED, RCI093-style)
    existing_alert_lip = find_existing_alert_lip(model) \
        if (notify_mail or notify_sftp) else None
    if has_main_sub:
        if not replace_existing:
            return {}
        _remove_main_exception_subprocesses(model, main)
    P = main.id
    ids = set(model.steps)
    pre = "EH_"
    while any(i.startswith(pre) for i in ids):     # collision-proof prefix
        pre += "X"

    ft = getattr(model, "_flow_target", None)
    if ft is None:
        model._flow_target = ft = {}

    def add(sid, kind, name, cfg, sub=""):
        s = Step(id=sid, kind=kind, name=name, process_id=P,
                 config=dict(cfg), parent_subprocess=sub)
        model.steps[sid] = s
        main.step_ids.append(sid)
        return s

    sub_id = pre + "SubProcess"
    add(sub_id, "ErrorEventSubProcessTemplate", "Exception Subprocess",
        _SUBPROC)
    # the parser's sequence carries top-level main-process steps only
    # (subprocess CHILDREN are excluded via parent_subprocess) — register the
    # subprocess itself so regenerate's reproduce check sees m1 == m2
    if hasattr(model, "sequence") and isinstance(model.sequence, list):
        model.sequence.append(sub_id)
    st = add(pre + "ErrorStart", "StartErrorEvent", "Error Start",
             _ERR_START, sub=sub_id)
    st.event_def = "error"
    gs = add(pre + "Capture", "Script", _n("GS Gold Error Capture"), _GS,
             sub=sub_id)
    chain = [st, gs]
    notify_files = {}
    if existing_alert_lip:
        # the flow already has a central alert LIP every subprocess calls —
        # call THAT one; do not inject a duplicate handler, endpoints, or
        # parameters (they all ship with the source already)
        call_cfg = dict(_ALERT_CALL)
        call_cfg["processId"] = existing_alert_lip
        chain.append(add(pre + "AlertCall", "ProcessCallElement",
                         _n("Exception Alert"), call_cfg, sub=sub_id))
        model._eh_reused_alert_lip = existing_alert_lip
        # the existing LIP brings its own adapters and parameters — make
        # sure the param appender does NOT add unused ALERT_* shells
        notify_mail = notify_sftp = False
    elif notify_mail or notify_sftp:
        # RCI093's REAL shape (and a tenant rule the user verified: Multicast
        # is NOT allowed inside an exception subprocess): the subprocess only
        # CALLS an injected Local Integration Process; the LIP owns the alert
        # chain — CM body → (parallel Multicast when both legs) → Send(s).
        from extractor.iflow_parser import (Endpoint, MessageFlow, Process,
                                            Step)
        names, reused = resolve_mail_param_names(existing_params)
        model._eh_reused_mail_roles = reused

        lip_id = pre + "AlertProcess"
        lip = Process(id=lip_id, name=_n("Gold Exception Alert"),
                      is_main=False)
        model.processes.append(lip)

        def addl(sid, kind, name, cfg):
            s = Step(id=sid, kind=kind, name=name, process_id=lip_id,
                     config=dict(cfg), parent_subprocess="")
            model.steps[sid] = s
            lip.step_ids.append(sid)
            return s

        lst = addl(pre + "AlertStart", "StartEvent", "Start", _LIP_START)
        lst.event_def = None      # plain LIP start — a messageEventDefinition
        #                           here is an editor error (user-verified)
        cm_cfg = dict(_CM_ALERT_BODY)
        cm_cfg["wrapContent"] = cm_cfg["wrapContent"].replace(
            "{company}", company or "company")
        lcm = addl(pre + "AlertBody", "Enricher", _n("CM Alert Body"),
                   cm_cfg)
        lend = addl(pre + "AlertEnd", "EndEvent", "End", _LIP_END)
        lend.event_def = None     # plain End — Message End can't go in a LIP
        lip_chain = [lst, lcm]
        legs = []
        if notify_mail:
            mail_cfg = dict(_MAIL_ADAPTER)
            mail_cfg["body"] = ALERT_MAIL_BODY_HTML.replace(
                "{company}", company or "company")
            for role, fld in _ADAPTER_FIELD_BY_ROLE.items():
                mail_cfg[fld] = "{{%s}}" % names[role]
            snd = addl(pre + "SendMail", "Send", _n("Send Error Mail"),
                       _SEND_STEP)
            part_id = pre + "MailReceiver"
            model.endpoints.append(Endpoint(
                id=part_id, direction="receiver", name="ALERT_MAIL",
                etype="EndpointRecevier"))       # verbatim SAP spelling
            model.message_flows.append(MessageFlow(
                id=pre + "MailFlow", name="Mail", source=snd.id,
                target=part_id, config=mail_cfg))
            if hasattr(model, "parameters"):
                model.parameters.update(
                    {k: v for k, v in ALERT_MAIL_PARAMS.items()
                     if _ROLE_TO_ALERT_INV.get(k) not in reused})
            legs.append([snd])
        if notify_sftp:
            snd_s = addl(pre + "SendSftp", "Send", _n("Send SFTP Error"),
                         _SEND_STEP)
            att = addl(pre + "Attach", "Script", _n("GS Log Attachment"),
                       _GS_ATTACH)
            part_s = pre + "SftpReceiver"
            model.endpoints.append(Endpoint(
                id=part_s, direction="receiver", name="ALERT_SFTP",
                etype="EndpointRecevier"))
            model.message_flows.append(MessageFlow(
                id=pre + "SftpFlow", name="SFTP", source=snd_s.id,
                target=part_s, config=dict(_SFTP_ADAPTER)))
            if hasattr(model, "parameters"):
                model.parameters.update(ALERT_SFTP_PARAMS)
            notify_files[ATTACH_SCRIPT_REL_PATH] = GOLD_ATTACH_SCRIPT
            legs.append([snd_s, att])            # RCI093 order: SFTP → attach
        if len(legs) == 2:
            mc = addl(pre + "Multicast", "Multicast",
                      _n("Multicast Alert"), _MULTICAST)
            lip_chain.append(mc)
        else:
            lip_chain.extend(legs[0])
            legs = []
        # wire the LIP: linear part, then (for multicast) each branch to End
        lip_full = lip_chain + ([] if legs else [lend])
        for i, (a, b) in enumerate(zip(lip_full, lip_full[1:])):
            fid = f"{pre}LipFlow_{i}"
            a.outgoing.append(fid)
            b.incoming.append(fid)
            ft[fid] = b.id
        for bi, leg in enumerate(legs):
            prev = lip_chain[-1]                 # the Multicast
            for si, stp in enumerate(leg + [lend]):
                fid = f"{pre}Branch{bi}_{si}"
                prev.outgoing.append(fid)
                stp.incoming.append(fid)
                ft[fid] = stp.id
                prev = stp
        # the subprocess only calls the LIP (RCI093's 'Exception Alert')
        call_cfg = dict(_ALERT_CALL)
        call_cfg["processId"] = lip_id
        chain.append(add(pre + "AlertCall", "ProcessCallElement",
                         _n("Exception Alert"), call_cfg, sub=sub_id))
    if variant == "message_end":
        chain.append(add(pre + "CM", "Enricher", _n("CM Error Response"),
                         _CM_ERROR_RESPONSE, sub=sub_id))
        end = add(pre + "End", "EndEvent", "End Message", _MSG_END,
                  sub=sub_id)
        end.event_def = "message"
    elif variant == "escalation_end":
        end = add(pre + "End", "EscalationEndEvent", "Escalation End",
                  _ESC_END, sub=sub_id)
        end.event_def = "escalation"
    else:
        end = add(pre + "End", "EndErrorEvent", "Error End", _ERR_END,
                  sub=sub_id)
        end.event_def = "error"
    chain.append(end)
    for i, (a, b) in enumerate(zip(chain, chain[1:])):
        fid = f"{pre}Flow_{i}"
        a.outgoing.append(fid)
        b.incoming.append(fid)
        ft[fid] = b.id
    model._eh_notify_mail = bool(notify_mail)
    model._eh_notify_sftp = bool(notify_sftp)
    files = {SCRIPT_REL_PATH: gold_capture_script(variant)}
    files.update(notify_files)
    return files


# ── notify-by-mail leg (decoded VERBATIM from Ric's own RCI093 production
#    alert: LIP3_Exception_Alert → Set Body CM → Send → Mail adapter v1.11,
#    every connection field externalized) ──────────────────────────────────
ALERT_MAIL_PARAMS = {
    "ALERT_MAIL_SERVER": "",
    "ALERT_MAIL_FROM": "",
    "ALERT_MAIL_TO": "",
    "ALERT_MAIL_SUBJECT": "${camelId}: ${property.ALERT_REASON}",
    "ALERT_MAIL_CRED": "",
    "ALERT_MAIL_AUTH": "None",
    "ALERT_MAIL_PROTECTION": "starttls_optional",   # RCI093's own default
    "ALERT_MAIL_PROXY": "none",
    "ALERT_MAIL_TIMEOUT": "30000",
}

# VERBATIM RCI093 'Set Body' shape. BUG FIX (user report: "the content
# modifier doesn't send anything to the email step"): the Content Modifier
# stores the message-body text in `wrapContent` (with bodyType=expression) —
# the earlier `bodyContent` key is dead config CPI ignores, so the body was
# never set. The headerTable creating ArtifactName=${camelId} is RCI093's
# own trick so downstream templates can reference the flow's name.
# {company} is replaced at generation time from Tab 0 · Company code.
_CM_ALERT_BODY = {
    "bodyType": "expression", "componentVersion": "1.6",
    "activityType": "Enricher",
    "cmdVariantUri": "ctype::FlowstepVariant/cname::Enricher/version::1.6.0",
    "headerTable": "<row><cell id='Action'>Create</cell>"
                   "<cell id='Type'>expression</cell>"
                   "<cell id='Value'>${camelId}</cell>"
                   "<cell id='Default'></cell>"
                   "<cell id='Name'>ArtifactName</cell>"
                   "<cell id='Datatype'></cell></row>",
    "wrapContent": "Technical error report of the Interface "
                   "${header.ArtifactName} in the {company} SAP CPI "
                   "environment.\n\nMessage Processing Log ID : "
                   "${property.SAP_MessageProcessingLogID}, DateTime : "
                   "${property.DateTime}\n\nError Reason : "
                   "${property.ALERT_REASON}\n\nError Message : "
                   "${exception.message}",
}

# RCI093's mail HTML body VERBATIM, company information stripped to the
# {company} token (user request) and the process line generalized to
# ${camelId}; Run Date/Time uses the DateTime property the capture script
# sets (RCI093 used a flow-specific Interface_run_date).
ALERT_MAIL_BODY_HTML = (
    '<span style="text-decoration: underline;">\n'
    '<b><font size="5">Interface Errors During Execution</font></b>\n'
    '</span>\n<p>\n<b>Company:</b> {company}\n</p>\n'
    '<p>\n<b>CPI Process:</b> ${camelId}\n</p>\n'
    '<p>\n<b>Run Date/Time: ${property.DateTime} </b> \n</p>\n \n'
    '<p>\n<b>Error Reason: </b> ${property.ALERT_REASON}\n</p>\n'
    '<p>\n<b>Error Message: </b> ${exception.message}\n</p>\n \n'
    '<p><i>This is a system-generated email. Please do not reply to this '
    'email address.</i></p>')

# VERBATIM RCI093 multicast (Set Body → parallel fan-out to mail + SFTP)
# VERBATIM RCI093: the exception subprocess does NOT multicast (the editor
# forbids Multicast inside exception subprocesses — user-verified on tenant);
# it calls a Local Integration Process and THAT fans out. These are the
# verbatim configs of RCI093's 'Exception Alert' ProcessCall and the LIP's
# own plain start/end events.
_ALERT_CALL = {"componentVersion": "1.0",
               "activityType": "ProcessCallElement",
               "subActivityType": "NonLoopingProcess",
               "cmdVariantUri": "ctype::FlowstepVariant/"
                                "cname::NonLoopingProcess/version::1.0.3"}
_LIP_START = {"activityType": "StartEvent",
              "cmdVariantUri": "ctype::FlowstepVariant/cname::StartEvent"}
_LIP_END = {"activityType": "EndEvent",
            "cmdVariantUri": "ctype::FlowstepVariant/cname::EndEvent"}

_MULTICAST = {"componentVersion": "1.1", "activityType": "Multicast",
              "cmdVariantUri":
                  "ctype::FlowstepVariant/cname::Multicast/version::1.1.1",
              "subActivityType": "parallel"}

# VERBATIM RCI093 LogAttachment.groovy (runs on the SFTP leg)
ATTACH_SCRIPT_REL_PATH = "src/main/resources/script/gold_log_attachment.groovy"
GOLD_ATTACH_SCRIPT = r"""import com.sap.gateway.ip.core.customdev.util.Message;

def Message processData(Message message) {
    try {
        def body = message.getBody(java.lang.String);
        def messageLog = messageLogFactory.getMessageLog(message)
        if (messageLog != null) {
            messageLog.addAttachmentAsString("ExceptionLog", body, "text/plain");
        }
        return message;
    } catch (Exception e) {
        def messageLog = messageLogFactory.getMessageLog(message);
        if (messageLog != null) {
            messageLog.addAttachmentAsString("ExceptionLog", e.getMessage(), "text/plain");
        }
        return message;
    }
}
"""
_GS_ATTACH = {"componentVersion": "1.1", "activityType": "Script",
              "subActivityType": "GroovyScript",
              "cmdVariantUri":
                  "ctype::FlowstepVariant/cname::GroovyScript/version::1.1.2",
              "script": "gold_log_attachment.groovy"}

ALERT_SFTP_PARAMS = {
    "ALERT_SFTP_HOST": "",
    "ALERT_SFTP_DIRECTORY": "errors/",
    "ALERT_SFTP_FILENAME": "cpi_error_${date:now:yyyyMMddHHmmss}.txt",
    "ALERT_SFTP_CRED": "",
    "ALERT_SFTP_AUTH": "user_password",          # RCI093's own default
    "ALERT_SFTP_TIMEOUT": "10000",
    "ALERT_SFTP_MAXRECONNECT": "3",
    "ALERT_SFTP_RECONNECTDELAY": "1000",
    "ALERT_SFTP_PROXY": "none",
}

# VERBATIM RCI093 SFTP receiver key set, alert-scoped placeholders
_SFTP_ADAPTER = {
    "ComponentNS": "sap", "ComponentSWCVId": "1.17.0",
    "ComponentSWCVName": "external", "ComponentType": "SFTP",
    "Description": "", "MessageProtocol": "File",
    "MessageProtocolVersion": "1.17.0", "Name": "SFTP_Error_Archive",
    "TransportProtocol": "SFTP", "TransportProtocolVersion": "1.17.0",
    "allowDeprecatedAlgorithms": "0",
    "authentication": "{{ALERT_SFTP_AUTH}}", "autoCreate": "1",
    "cmdVariantUri": "ctype::AdapterVariant/cname::sap:SFTP/tp::SFTP/"
                     "mp::File/direction::Receiver/version::1.13.1",
    "componentVersion": "1.13",
    "connectTimeout": "{{ALERT_SFTP_TIMEOUT}}",
    "credential_name": "{{ALERT_SFTP_CRED}}", "direction": "Receiver",
    "disconnect": "1", "fastExistsCheck": "0",
    "fileAppendTimeStamp": "0", "fileExist": "Override",
    "fileName": "{{ALERT_SFTP_FILENAME}}", "flatten": "0",
    "host": "{{ALERT_SFTP_HOST}}", "location_id": "",
    "maximumReconnectAttempts": "{{ALERT_SFTP_MAXRECONNECT}}",
    "path": "{{ALERT_SFTP_DIRECTORY}}", "privateKeyAlias": "",
    "proxyAlias": "", "proxyHost": "", "proxyPort": "8080",
    "proxyProtocol": "socks5", "proxyType": "{{ALERT_SFTP_PROXY}}",
    "reconnectDelay": "{{ALERT_SFTP_RECONNECTDELAY}}",
    "sftpSecEnabled": "0", "stepwise": "1", "system": "ALERT_SFTP",
    "tempFileName": "${file:name}.tmp", "useTempFile": "0",
    "username": "",
}

# RCI093's externalized mail family — when the SOURCE flow already defines
# these (≥3 present, or any *cred* match for the credential alone), the
# injected adapter reuses the client's OWN parameter names so their already-
# configured values (credential included) flow straight in (user request:
# "if the credential for email already exists, use it").
_RCI_FAMILY = {
    "server": "ConnectionError_MailAddress",
    "from": "ConnectionError_MailFrom",
    "to": "ConnectionError_MailTo",
    "subject": "ConnectionError_MailSubject",
    "cred": "ConnectionError_MailCred",
    "auth": "ConnectionError_MailAuth",
    "protection": "ConnectionError_MailProtection",
    "proxy": "ConnectionError_MailProxy",
    "timeout": "ConnectionError_MailTimeOut",
}
_ROLE_TO_ALERT = {
    "server": "ALERT_MAIL_SERVER", "from": "ALERT_MAIL_FROM",
    "to": "ALERT_MAIL_TO", "subject": "ALERT_MAIL_SUBJECT",
    "cred": "ALERT_MAIL_CRED", "auth": "ALERT_MAIL_AUTH",
    "protection": "ALERT_MAIL_PROTECTION", "proxy": "ALERT_MAIL_PROXY",
    "timeout": "ALERT_MAIL_TIMEOUT",
}
_ROLE_TO_ALERT_INV.update({v: k for k, v in _ROLE_TO_ALERT.items()})
_ADAPTER_FIELD_BY_ROLE = {
    "server": "server", "from": "from", "to": "to", "subject": "subject",
    "cred": "user", "auth": "auth", "protection": "ssl", "proxy": "proxyType",
    "timeout": "timeout",
}


def resolve_mail_param_names(existing_params: str | None) -> tuple:
    """Returns (role→placeholder-name map, set of roles REUSED from the
    source's own parameters). Reuse logic: the full RCI093-style family when
    ≥3 of its names are present; otherwise any credential-looking param
    ('mail*cred' / 'cred*mail' / 'email*cred', case-insensitive) for the
    credential role alone."""
    import re as _re
    names = dict(_ROLE_TO_ALERT)
    reused: set = set()
    if not existing_params:
        return names, reused
    keys = set()
    for ln in existing_params.splitlines():
        if "=" in ln and not ln.lstrip().startswith("#"):
            keys.add(ln.split("=", 1)[0].strip().replace("\\ ", " "))
    fam_hits = {r: p for r, p in _RCI_FAMILY.items() if p in keys}
    if len(fam_hits) >= 3:
        for role, pname in fam_hits.items():
            names[role] = pname
            reused.add(role)
        return names, reused
    for k in keys:
        if _re.search(r"(?i)(mail.*cred|cred.*mail|email.*cred)", k):
            names["cred"] = k
            reused.add("cred")
            break
    return names, reused
_SEND_STEP = {"componentVersion": "1.0", "activityType": "Send",
              "cmdVariantUri":
                  "ctype::FlowstepVariant/cname::Send/version::1.0.4"}
_MAIL_ADAPTER = {
    "ComponentNS": "com.sap.it.ide.mail.ui.namespace2",
    "ComponentSWCVId": "com.sap.it.ide.mail.ui.archive2",
    "ComponentSWCVName": "com.sap.it.ide.mail.ui.archive2",
    "ComponentType": "Mail", "Description": "",
    "MessageProtocol": "None", "MessageProtocolVersion": "1.0",
    "Name": "Mail_Alert", "TransportProtocol": "SMTP",
    "TransportProtocolVersion": "1.0",
    "attachmentTransferEncoding": "auto", "attachments": "",
    "auth": "{{ALERT_MAIL_AUTH}}", "bcc": "", "cc": "",
    "body": ALERT_MAIL_BODY_HTML,
    "cmdVariantUri": "ctype::AdapterVariant/cname::sap:Mail/tp::SMTP/"
                     "mp::None/direction::Receiver/version::1.11.0",
    "componentVersion": "1.11", "content_encoding": "UTF-8",
    "content_type": "text/html", "direction": "Receiver",
    "encrypt.smime.aes.gcm.keysize": "128", "encrypt.smime.aes.keysize": "128",
    "encrypt.smime.algorithm": "aes", "encrypt.smime.des.keysize": "128",
    "encrypt.smime.keys": "", "encrypt.type": "none",
    "from": "{{ALERT_MAIL_FROM}}", "keep_attachments": "0", "locationId": "",
    "proxyAlias": "", "proxyHost": "", "proxyPort": "8080",
    "proxyProtocol": "socks5", "proxyType": "{{ALERT_MAIL_PROXY}}",
    "server": "{{ALERT_MAIL_SERVER}}",
    "signature.smime.clearText": "1", "signature.smime.table": "",
    "ssl": "{{ALERT_MAIL_PROTECTION}}",
    "subject": "{{ALERT_MAIL_SUBJECT}}", "system": "ALERT_MAIL",
    "timeout": "{{ALERT_MAIL_TIMEOUT}}", "to": "{{ALERT_MAIL_TO}}",
    "tokenCredential": "", "user": "{{ALERT_MAIL_CRED}}",
}


def append_alert_params(files: dict, mail: bool = True,
                        sftp: bool = False, reused_roles=()) -> None:
    """Append the injected ALERT_* externalized parameters to whatever
    parameter pair ships with the bundle (the SOURCE pair ships verbatim and
    wins, so injected params must be appended; formats verbatim from real
    exports). Roles reused from the source's own family are skipped — their
    params already exist with the client's configured values."""
    pp = "src/main/resources/parameters.prop"
    pd = "src/main/resources/parameters.propdef"
    prop = files.get(pp) or "#\n"
    pdef = files.get(pd) or ('<?xml version="1.0" encoding="UTF-8" '
                             'standalone="no"?><parameters></parameters>')
    wanted = {}
    if mail:
        wanted.update({k: v for k, v in ALERT_MAIL_PARAMS.items()
                       if _ROLE_TO_ALERT_INV.get(k) not in set(reused_roles)})
    if sftp:
        wanted.update(ALERT_SFTP_PARAMS)
    import re as _re
    for name, default in wanted.items():
        if name not in prop:
            prop = prop.rstrip("\n") + f"\n{name}={default}\n"
        elif default:
            # the generator may have synthesized an EMPTY shell from
            # model.parameters before this runs — fill the default in
            prop = _re.sub(rf"^{name}=$", f"{name}={default}",
                           prop, count=1, flags=_re.M)
        if f"<name>{name}</name>" not in pdef:
            entry = ("<parameter>\n    <key/>\n"
                     f"    <name>{name}</name>\n"
                     "    <type>xsd:string</type>\n"
                     "    <isRequired>false</isRequired>\n"
                     "    <constraint/>\n    <description/>\n"
                     "    <additionalMetadata/>\n  </parameter>")
            pdef = pdef.replace("</parameters>", entry + "</parameters>", 1)
    files[pp] = prop
    files[pd] = pdef

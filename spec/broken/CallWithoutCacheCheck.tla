------------------------------ MODULE CallWithoutCacheCheck ------------------------------
EXTENDS Naturals, FiniteSets

CONSTANTS Jobs, Renderers, StepKeys, MaxAttempts

VARIABLES state, attempt, renderedAttempt, rendering, done, cached, calls, idemKeys, ledgerLines

vars == <<state, attempt, renderedAttempt, rendering, done, cached, calls, idemKeys, ledgerLines>>

Idle == "idle"

States == {"queued", "rendering", "rendered", "verified", "failed", "retried"}

Moves ==
    { <<"queued", "rendering">>, <<"rendering", "rendered">>, <<"rendering", "failed">>,
      <<"rendered", "verified">>, <<"rendered", "failed">>, <<"failed", "retried">>,
      <<"retried", "rendering">> }

IdemKey(j, k) == <<k>>

MaxRuns == Cardinality(Jobs) * MaxAttempts

RenderersOf(j) == {r \in Renderers : rendering[r] = j}

Init ==
    /\ state = [j \in Jobs |-> "queued"]
    /\ attempt = [j \in Jobs |-> 1]
    /\ renderedAttempt = [j \in Jobs |-> 0]
    /\ rendering = [r \in Renderers |-> Idle]
    /\ done = [r \in Renderers |-> {}]
    /\ cached = {}
    /\ calls = [k \in StepKeys |-> 0]
    /\ idemKeys = {}
    /\ ledgerLines = [k \in StepKeys |-> 0]

Claim(r, j) ==
    /\ rendering[r] = Idle
    /\ state[j] \in {"queued", "retried"}
    /\ state' = [state EXCEPT ![j] = "rendering"]
    /\ rendering' = [rendering EXCEPT ![r] = j]
    /\ done' = [done EXCEPT ![r] = {}]
    /\ UNCHANGED <<attempt, renderedAttempt, cached, calls, idemKeys, ledgerLines>>

RunPaidStep(r, k) ==
    /\ rendering[r] # Idle
    /\ k \notin done[r]
    /\ calls' = [calls EXCEPT ![k] = @ + 1]
    /\ cached' = cached \cup {k}
    /\ LET j == rendering[r] IN
        /\ ledgerLines' = IF IdemKey(j, k) \in idemKeys THEN ledgerLines ELSE [ledgerLines EXCEPT ![k] = @ + 1]
        /\ idemKeys' = idemKeys \cup {IdemKey(j, k)}
    /\ done' = [done EXCEPT ![r] = @ \cup {k}]
    /\ UNCHANGED <<state, attempt, renderedAttempt, rendering>>

Finish(r) ==
    /\ rendering[r] # Idle
    /\ done[r] = StepKeys
    /\ LET j == rendering[r] IN
        /\ state' = [state EXCEPT ![j] = "rendered"]
        /\ renderedAttempt' = [renderedAttempt EXCEPT ![j] = attempt[j]]
    /\ rendering' = [rendering EXCEPT ![r] = Idle]
    /\ UNCHANGED <<attempt, done, cached, calls, idemKeys, ledgerLines>>

RenderFails(r) ==
    /\ rendering[r] # Idle
    /\ state' = [state EXCEPT ![rendering[r]] = "failed"]
    /\ rendering' = [rendering EXCEPT ![r] = Idle]
    /\ UNCHANGED <<attempt, renderedAttempt, done, cached, calls, idemKeys, ledgerLines>>

Crash(r) ==
    /\ rendering[r] # Idle
    /\ rendering' = [rendering EXCEPT ![r] = Idle]
    /\ UNCHANGED <<state, attempt, renderedAttempt, done, cached, calls, idemKeys, ledgerLines>>

Recover(j) ==
    /\ state[j] = "rendering"
    /\ RenderersOf(j) = {}
    /\ state' = [state EXCEPT ![j] = "failed"]
    /\ UNCHANGED <<attempt, renderedAttempt, rendering, done, cached, calls, idemKeys, ledgerLines>>

Retry(j) ==
    /\ state[j] = "failed"
    /\ attempt[j] < MaxAttempts
    /\ state' = [state EXCEPT ![j] = "retried"]
    /\ attempt' = [attempt EXCEPT ![j] = @ + 1]
    /\ UNCHANGED <<renderedAttempt, rendering, done, cached, calls, idemKeys, ledgerLines>>

VerifyPasses(j) ==
    /\ state[j] = "rendered"
    /\ state' = [state EXCEPT ![j] = "verified"]
    /\ UNCHANGED <<attempt, renderedAttempt, rendering, done, cached, calls, idemKeys, ledgerLines>>

VerifyRejects(j) ==
    /\ state[j] = "rendered"
    /\ state' = [state EXCEPT ![j] = "failed"]
    /\ UNCHANGED <<attempt, renderedAttempt, rendering, done, cached, calls, idemKeys, ledgerLines>>

Settled(j) == state[j] = "verified" \/ (state[j] = "failed" /\ attempt[j] = MaxAttempts)

AllSettled ==
    /\ \A j \in Jobs : Settled(j)
    /\ UNCHANGED vars

Next ==
    \/ \E r \in Renderers, j \in Jobs : Claim(r, j)
    \/ \E r \in Renderers, k \in StepKeys : RunPaidStep(r, k)
    \/ \E r \in Renderers : Finish(r)
    \/ \E r \in Renderers : RenderFails(r)
    \/ \E r \in Renderers : Crash(r)
    \/ \E j \in Jobs : Recover(j)
    \/ \E j \in Jobs : Retry(j)
    \/ \E j \in Jobs : VerifyPasses(j)
    \/ \E j \in Jobs : VerifyRejects(j)
    \/ AllSettled

Fairness ==
    /\ \A r \in Renderers, j \in Jobs : WF_vars(Claim(r, j))
    /\ \A r \in Renderers, k \in StepKeys : WF_vars(RunPaidStep(r, k))
    /\ \A r \in Renderers : WF_vars(Finish(r))
    /\ \A j \in Jobs : WF_vars(Recover(j))
    /\ \A j \in Jobs : WF_vars(Retry(j))
    /\ \A j \in Jobs : WF_vars(VerifyPasses(j) \/ VerifyRejects(j))

Spec == Init /\ [][Next]_vars /\ Fairness

TypeOK ==
    /\ state \in [Jobs -> States]
    /\ attempt \in [Jobs -> 1..MaxAttempts]
    /\ renderedAttempt \in [Jobs -> 0..MaxAttempts]
    /\ rendering \in [Renderers -> Jobs \cup {Idle}]
    /\ done \in [Renderers -> SUBSET StepKeys]
    /\ cached \subseteq StepKeys
    /\ calls \in [StepKeys -> 0..MaxRuns]
    /\ idemKeys \subseteq {<<k>> : k \in StepKeys} \cup (Jobs \X StepKeys)
    /\ ledgerLines \in [StepKeys -> 0..MaxRuns]

NoDoubleCharge == \A k \in StepKeys : ledgerLines[k] <= 1

OneCallPerStepKey == \A k \in StepKeys : calls[k] <= 1

RenderedIsFullyCharged ==
    \A j \in Jobs : state[j] \in {"rendered", "verified"} => \A k \in StepKeys : ledgerLines[k] = 1

VerifiedImpliesRendered == \A j \in Jobs : state[j] = "verified" => renderedAttempt[j] = attempt[j]

OneActiveRenderPerJob ==
    \A j \in Jobs :
        /\ Cardinality(RenderersOf(j)) <= 1
        /\ RenderersOf(j) # {} => state[j] = "rendering"

OnlyDeclaredMoves ==
    [][\A j \in Jobs : state'[j] # state[j] => <<state[j], state'[j]>> \in Moves]_vars

EveryJobSettles == \A j \in Jobs : <>[]Settled(j)
==============================================================================

------------------------------ MODULE IdemKeyWithJob ------------------------------
EXTENDS Naturals, FiniteSets

CONSTANTS Jobs, Renderers, StepKeys, MaxAttempts, MaxRemovals

VARIABLES state, attempt, renderedAttempt, rendering, done, cached, calls, removals, idemKeys, ledgerLines

vars == <<state, attempt, renderedAttempt, rendering, done, cached, calls, removals, idemKeys, ledgerLines>>

Idle == "idle"

States == {"queued", "rendering", "rendered", "verified", "failed", "retried"}

Moves ==
    { <<"queued", "rendering">>, <<"rendering", "rendered">>, <<"rendering", "failed">>,
      <<"rendered", "verified">>, <<"rendered", "failed">>, <<"failed", "retried">>,
      <<"retried", "rendering">> }

IdemKey(j, k, n) == <<j, k, n>>

MaxRuns == Cardinality(Jobs) * MaxAttempts

Counts == 0..(MaxRuns + MaxRemovals)

RenderersOf(j) == {r \in Renderers : rendering[r] = j}

Init ==
    /\ state = [j \in Jobs |-> "queued"]
    /\ attempt = [j \in Jobs |-> 1]
    /\ renderedAttempt = [j \in Jobs |-> 0]
    /\ rendering = [r \in Renderers |-> Idle]
    /\ done = [r \in Renderers |-> {}]
    /\ cached = {}
    /\ calls = [k \in StepKeys |-> 0]
    /\ removals = [k \in StepKeys |-> 0]
    /\ idemKeys = {}
    /\ ledgerLines = [k \in StepKeys |-> 0]

Claim(r, j) ==
    /\ rendering[r] = Idle
    /\ state[j] \in {"queued", "retried"}
    /\ state' = [state EXCEPT ![j] = "rendering"]
    /\ rendering' = [rendering EXCEPT ![r] = j]
    /\ done' = [done EXCEPT ![r] = {}]
    /\ UNCHANGED <<attempt, renderedAttempt, cached, calls, removals, idemKeys, ledgerLines>>

RunPaidStep(r, k) ==
    /\ rendering[r] # Idle
    /\ k \notin done[r]
    /\ calls' = IF k \in cached THEN calls ELSE [calls EXCEPT ![k] = @ + 1]
    /\ cached' = cached \cup {k}
    /\ LET key == IdemKey(rendering[r], k, calls'[k]) IN
        /\ ledgerLines' = IF key \in idemKeys THEN ledgerLines ELSE [ledgerLines EXCEPT ![k] = @ + 1]
        /\ idemKeys' = idemKeys \cup {key}
    /\ done' = [done EXCEPT ![r] = @ \cup {k}]
    /\ UNCHANGED <<state, attempt, renderedAttempt, rendering, removals>>

Remove(k) ==
    /\ k \in cached
    /\ removals[k] < MaxRemovals
    /\ cached' = cached \ {k}
    /\ removals' = [removals EXCEPT ![k] = @ + 1]
    /\ UNCHANGED <<state, attempt, renderedAttempt, rendering, done, calls, idemKeys, ledgerLines>>

Finish(r) ==
    /\ rendering[r] # Idle
    /\ done[r] = StepKeys
    /\ LET j == rendering[r] IN
        /\ state' = [state EXCEPT ![j] = "rendered"]
        /\ renderedAttempt' = [renderedAttempt EXCEPT ![j] = attempt[j]]
    /\ rendering' = [rendering EXCEPT ![r] = Idle]
    /\ UNCHANGED <<attempt, done, cached, calls, removals, idemKeys, ledgerLines>>

RenderFails(r) ==
    /\ rendering[r] # Idle
    /\ state' = [state EXCEPT ![rendering[r]] = "failed"]
    /\ rendering' = [rendering EXCEPT ![r] = Idle]
    /\ UNCHANGED <<attempt, renderedAttempt, done, cached, calls, removals, idemKeys, ledgerLines>>

Crash(r) ==
    /\ rendering[r] # Idle
    /\ rendering' = [rendering EXCEPT ![r] = Idle]
    /\ UNCHANGED <<state, attempt, renderedAttempt, done, cached, calls, removals, idemKeys, ledgerLines>>

Recover(j) ==
    /\ state[j] = "rendering"
    /\ RenderersOf(j) = {}
    /\ state' = [state EXCEPT ![j] = "failed"]
    /\ UNCHANGED <<attempt, renderedAttempt, rendering, done, cached, calls, removals, idemKeys, ledgerLines>>

Retry(j) ==
    /\ state[j] = "failed"
    /\ attempt[j] < MaxAttempts
    /\ state' = [state EXCEPT ![j] = "retried"]
    /\ attempt' = [attempt EXCEPT ![j] = @ + 1]
    /\ UNCHANGED <<renderedAttempt, rendering, done, cached, calls, removals, idemKeys, ledgerLines>>

VerifyPasses(j) ==
    /\ state[j] = "rendered"
    /\ state' = [state EXCEPT ![j] = "verified"]
    /\ UNCHANGED <<attempt, renderedAttempt, rendering, done, cached, calls, removals, idemKeys, ledgerLines>>

VerifyRejects(j) ==
    /\ state[j] = "rendered"
    /\ state' = [state EXCEPT ![j] = "failed"]
    /\ UNCHANGED <<attempt, renderedAttempt, rendering, done, cached, calls, removals, idemKeys, ledgerLines>>

Settled(j) == state[j] = "verified" \/ (state[j] = "failed" /\ attempt[j] = MaxAttempts)

AllSettled ==
    /\ \A j \in Jobs : Settled(j)
    /\ UNCHANGED vars

Next ==
    \/ \E r \in Renderers, j \in Jobs : Claim(r, j)
    \/ \E r \in Renderers, k \in StepKeys : RunPaidStep(r, k)
    \/ \E k \in StepKeys : Remove(k)
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
    /\ calls \in [StepKeys -> Counts]
    /\ removals \in [StepKeys -> 0..MaxRemovals]
    /\ idemKeys \subseteq UNION {StepKeys \X Counts, Jobs \X StepKeys \X Counts, {<<n>> : n \in StepKeys \cup Counts}}
    /\ ledgerLines \in [StepKeys -> Counts]

NoDoubleCharge == \A k \in StepKeys : ledgerLines[k] <= calls[k]

CalledAgainOnlyAfterRemoval == \A k \in StepKeys : calls[k] <= removals[k] + 1

RenderedIsFullyCharged ==
    \A j \in Jobs : state[j] \in {"rendered", "verified"} =>
        \A k \in StepKeys : calls[k] >= 1 /\ ledgerLines[k] = calls[k]

VerifiedImpliesRendered == \A j \in Jobs : state[j] = "verified" => renderedAttempt[j] = attempt[j]

OneActiveRenderPerJob ==
    \A j \in Jobs :
        /\ Cardinality(RenderersOf(j)) <= 1
        /\ RenderersOf(j) # {} => state[j] = "rendering"

OnlyDeclaredMoves ==
    [][\A j \in Jobs : state'[j] # state[j] => <<state[j], state'[j]>> \in Moves]_vars

EveryJobSettles == \A j \in Jobs : <>[]Settled(j)
==============================================================================

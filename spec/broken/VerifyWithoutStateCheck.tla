------------------------------ MODULE VerifyWithoutStateCheck ------------------------------
EXTENDS Naturals, FiniteSets

CONSTANTS Jobs, Renderers, PaidSteps, MaxAttempts

VARIABLES state, attempt, renderedAttempt, rendering, done, idemKeys, ledgerLines

vars == <<state, attempt, renderedAttempt, rendering, done, idemKeys, ledgerLines>>

Idle == "idle"

States == {"queued", "rendering", "rendered", "verified", "failed", "retried"}

Moves ==
    { <<"queued", "rendering">>, <<"rendering", "rendered">>, <<"rendering", "failed">>,
      <<"rendered", "verified">>, <<"rendered", "failed">>, <<"failed", "retried">>,
      <<"retried", "rendering">> }

IdemKey(j, s) == <<j, s>>

RenderersOf(j) == {r \in Renderers : rendering[r] = j}

Init ==
    /\ state = [j \in Jobs |-> "queued"]
    /\ attempt = [j \in Jobs |-> 1]
    /\ renderedAttempt = [j \in Jobs |-> 0]
    /\ rendering = [r \in Renderers |-> Idle]
    /\ done = [r \in Renderers |-> {}]
    /\ idemKeys = {}
    /\ ledgerLines = [j \in Jobs |-> [s \in PaidSteps |-> 0]]

Claim(r, j) ==
    /\ rendering[r] = Idle
    /\ state[j] \in {"queued", "retried"}
    /\ state' = [state EXCEPT ![j] = "rendering"]
    /\ rendering' = [rendering EXCEPT ![r] = j]
    /\ done' = [done EXCEPT ![r] = {}]
    /\ UNCHANGED <<attempt, renderedAttempt, idemKeys, ledgerLines>>

RunPaidStep(r, s) ==
    /\ rendering[r] # Idle
    /\ s \notin done[r]
    /\ LET j == rendering[r] IN
        /\ ledgerLines' = IF IdemKey(j, s) \in idemKeys THEN ledgerLines ELSE [ledgerLines EXCEPT ![j][s] = @ + 1]
        /\ idemKeys' = idemKeys \cup {IdemKey(j, s)}
    /\ done' = [done EXCEPT ![r] = @ \cup {s}]
    /\ UNCHANGED <<state, attempt, renderedAttempt, rendering>>

Finish(r) ==
    /\ rendering[r] # Idle
    /\ done[r] = PaidSteps
    /\ LET j == rendering[r] IN
        /\ state' = [state EXCEPT ![j] = "rendered"]
        /\ renderedAttempt' = [renderedAttempt EXCEPT ![j] = attempt[j]]
    /\ rendering' = [rendering EXCEPT ![r] = Idle]
    /\ UNCHANGED <<attempt, done, idemKeys, ledgerLines>>

RenderFails(r) ==
    /\ rendering[r] # Idle
    /\ state' = [state EXCEPT ![rendering[r]] = "failed"]
    /\ rendering' = [rendering EXCEPT ![r] = Idle]
    /\ UNCHANGED <<attempt, renderedAttempt, done, idemKeys, ledgerLines>>

Crash(r) ==
    /\ rendering[r] # Idle
    /\ rendering' = [rendering EXCEPT ![r] = Idle]
    /\ UNCHANGED <<state, attempt, renderedAttempt, done, idemKeys, ledgerLines>>

Recover(j) ==
    /\ state[j] = "rendering"
    /\ RenderersOf(j) = {}
    /\ state' = [state EXCEPT ![j] = "failed"]
    /\ UNCHANGED <<attempt, renderedAttempt, rendering, done, idemKeys, ledgerLines>>

Retry(j) ==
    /\ state[j] = "failed"
    /\ attempt[j] < MaxAttempts
    /\ state' = [state EXCEPT ![j] = "retried"]
    /\ attempt' = [attempt EXCEPT ![j] = @ + 1]
    /\ UNCHANGED <<renderedAttempt, rendering, done, idemKeys, ledgerLines>>

VerifyPasses(j) ==
    /\ renderedAttempt[j] > 0
    /\ state' = [state EXCEPT ![j] = "verified"]
    /\ UNCHANGED <<attempt, renderedAttempt, rendering, done, idemKeys, ledgerLines>>

VerifyRejects(j) ==
    /\ state[j] = "rendered"
    /\ state' = [state EXCEPT ![j] = "failed"]
    /\ UNCHANGED <<attempt, renderedAttempt, rendering, done, idemKeys, ledgerLines>>

Settled(j) == state[j] = "verified" \/ (state[j] = "failed" /\ attempt[j] = MaxAttempts)

AllSettled ==
    /\ \A j \in Jobs : Settled(j)
    /\ UNCHANGED vars

Next ==
    \/ \E r \in Renderers, j \in Jobs : Claim(r, j)
    \/ \E r \in Renderers, s \in PaidSteps : RunPaidStep(r, s)
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
    /\ \A r \in Renderers, s \in PaidSteps : WF_vars(RunPaidStep(r, s))
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
    /\ done \in [Renderers -> SUBSET PaidSteps]
    /\ idemKeys \subseteq Jobs \X PaidSteps
    /\ ledgerLines \in [Jobs -> [PaidSteps -> 0..MaxAttempts]]

NoDoubleCharge == \A j \in Jobs, s \in PaidSteps : ledgerLines[j][s] <= 1

RenderedIsFullyCharged ==
    \A j \in Jobs : state[j] \in {"rendered", "verified"} => \A s \in PaidSteps : ledgerLines[j][s] = 1

VerifiedImpliesRendered == \A j \in Jobs : state[j] = "verified" => renderedAttempt[j] = attempt[j]

OneActiveRenderPerJob ==
    \A j \in Jobs :
        /\ Cardinality(RenderersOf(j)) <= 1
        /\ RenderersOf(j) # {} => state[j] = "rendering"

OnlyDeclaredMoves ==
    [][\A j \in Jobs : state'[j] # state[j] => <<state[j], state'[j]>> \in Moves]_vars

EveryJobSettles == \A j \in Jobs : <>[]Settled(j)
==============================================================================

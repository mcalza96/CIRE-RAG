-- Create table for AI Audit Logs (RAG Telemetry)
create table if not exists "public"."ai_audit_logs" (
    "id" uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    "trace_id" uuid NOT NULL,
    "created_at" timestamp with time zone DEFAULT now() NOT NULL,
    "actors" jsonb DEFAULT '{}'::jsonb, -- Output of each agent (Strategist, Journalist, Critic)
    "retrieval_stats" jsonb DEFAULT '{}'::jsonb, -- Chunks retrieved, scores, execution time
    "tokens_used" jsonb DEFAULT '{}'::jsonb, -- Token usage (prompt/completion)
    "latency_ms" integer, -- Total execution time
    "status" text CHECK (status IN ('success', 'failed', 'flagged')),
    "feedback" jsonb DEFAULT '{}'::jsonb -- User feedback (rating, comment)
);

-- Enable RLS
alter table "public"."ai_audit_logs" enable row level security;

-- Policies
do $$
begin
    drop policy if exists "Enable insert for authenticated users only" on "public"."ai_audit_logs";
    drop policy if exists "Enable read for authenticated users only" on "public"."ai_audit_logs";
end $$;

create policy "Enable insert for authenticated users only"
on "public"."ai_audit_logs"
for insert
to authenticated
with check (true);

create policy "Enable read for authenticated users only"
on "public"."ai_audit_logs"
for select
to authenticated
using (true);

-- Index for faster trace lookups
create index if not exists ai_audit_logs_trace_id_idx on "public"."ai_audit_logs" (trace_id);
create index if not exists ai_audit_logs_created_at_idx on "public"."ai_audit_logs" (created_at desc);

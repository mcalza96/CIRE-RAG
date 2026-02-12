-- Create Enums
create type ai_trace_type as enum ('curriculum_structure', 'atom_research', 'lesson_draft');
create type ai_user_feedback as enum ('thumbs_up', 'thumbs_down', 'flagged');

-- Create Table
create table if not exists ai_generation_traces (
    id uuid primary key default gen_random_uuid(),
    created_at timestamptz default now(),
    
    trace_type ai_trace_type not null,
    model_used text not null,
    
    -- Inputs & Outputs
    input_context_snapshot jsonb, -- Blueprint or Prompt Context
    output_content text,
    
    -- Metrics
    latency_ms integer,
    token_usage jsonb, -- { prompt: integer, completion: integer, total: integer }
    
    -- Quality & Integrity
    integrity_score float check (integrity_score >= 0.0 and integrity_score <= 1.0),
    integrity_report jsonb, -- Details of missing/hallucinated atoms
    
    -- Feedback
    user_feedback ai_user_feedback,
    feedback_notes text,
    
    -- Context Metadata
    course_id uuid references courses(id),
    unit_id uuid references course_units(id),
    user_id uuid references auth.users(id)
);

-- Indexes
create index ai_traces_type_idx on ai_generation_traces(trace_type);
create index ai_traces_integrity_idx on ai_generation_traces(integrity_score);
create index ai_traces_created_at_idx on ai_generation_traces(created_at desc);

-- RLS Policies
alter table ai_generation_traces enable row level security;

-- Only authenticated users can insert traces (system wide or per user)
create policy "Users can insert their own traces"
    on ai_generation_traces for insert
    to authenticated
    with check (true); 

-- Teachers can view their own traces or traces for their courses (simplified for now to own traces)
create policy "Users can view their own traces"
    on ai_generation_traces for select
    to authenticated
    using (auth.uid() = user_id);

-- Update Feedback Policy
create policy "Users can update feedback on their own traces"
    on ai_generation_traces for update
    to authenticated
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

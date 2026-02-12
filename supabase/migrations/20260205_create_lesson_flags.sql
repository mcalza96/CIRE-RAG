-- Create lesson_flags table for tracking pedagogical alerts
create table if not exists lesson_flags (
  id uuid primary key default gen_random_uuid(),
  lesson_id uuid not null references lessons(id) on delete cascade,
  student_id uuid not null references auth.users(id) on delete cascade,
  reason text not null,
  details jsonb default '{}'::jsonb,
  created_at timestamptz default now()
);

-- Enable RLS
alter table lesson_flags enable row level security;

-- Policies
create policy "Teachers can view lesson flags"
  on lesson_flags for select
  using (
    exists (
      select 1 from courses c
      join lessons l on l.course_id = c.id
      where l.id = lesson_flags.lesson_id
      and c.teacher_id = auth.uid()
    )
  );

create policy "Students can insert lesson flags (system logic)"
  on lesson_flags for insert
  with check (
    auth.uid() = student_id
  );

-- Index for querying flags by student and lesson
create index idx_lesson_flags_student_lesson on lesson_flags(student_id, lesson_id);
create index idx_lesson_flags_created_at on lesson_flags(created_at desc);

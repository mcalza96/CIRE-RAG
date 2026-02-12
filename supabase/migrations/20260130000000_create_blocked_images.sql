
-- Create blocked_images table for Safe Search Enforcement
create table if not exists blocked_images (
  id uuid primary key default gen_random_uuid(),
  provider_image_id text not null,
  provider_source text not null,
  reason text,
  reported_by uuid references auth.users(id),
  created_at timestamp with time zone default timezone('utc'::text, now()) not null,
  
  -- Constraint to ensure provider_source is valid
  constraint valid_provider check (provider_source in ('unsplash', 'pixabay'))
);

-- Composite Index for fast lookups during search (Batch Filtering)
create index idx_blocked_images_lookup on blocked_images (provider_source, provider_image_id);

-- Enable Row Level Security
alter table blocked_images enable row level security;

-- RLS Policies
-- 1. Everyone (Anon & Authenticated) can read the blacklist (Required for filtering search results)
create policy "Allow public read access"
  on blocked_images for select
  using (true);

-- 2. Only Authenticated Teachers can report images (Insert)
-- Assuming a 'teacher' claim or role, but for now allowing any authenticated user to report for broader safety coverage
-- Ideally, checking for role: (auth.jwt() ->> 'role') = 'teacher' if setup, but standard auth is safer for generic start
create policy "Allow authenticated insert"
  on blocked_images for insert
  with check (auth.role() = 'authenticated');

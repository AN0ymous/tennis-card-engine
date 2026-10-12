-- Tennis Card Engine: accounts and what each person keeps.
--
-- Run once, in the Supabase dashboard: SQL Editor -> New query -> paste ->
-- Run. It is safe to run again; every statement skips what already exists.
--
-- Accounts themselves (email, password hash, reset codes) live in Supabase's
-- own auth schema and are never touched here. These tables hold what a
-- signed-in person saves, and row-level security means a person can read
-- and write their own rows only -- the public key in the page cannot reach
-- anybody else's, whatever the page asks for.

-- One row per account: the switches for the emails. Created on first use by
-- the page, never required.
create table if not exists public.profiles (
  user_id      uuid primary key references auth.users (id) on delete cascade,
  email        text not null,
  daily_email  boolean not null default false,   -- the daily board email
  alerts       boolean not null default true,    -- a mail when a saved search matches
  created_at   timestamptz not null default now()
);

-- A starred card. `entry` is exactly what the page keeps in the browser for a
-- saved card (the card plus savedAt and the owner's own sold/active mark), so
-- the saved page draws it unchanged whichever device signed in.
create table if not exists public.saved_listings (
  user_id   uuid not null default auth.uid() references auth.users (id) on delete cascade,
  key       text not null,                        -- the listing link, as the page keys it
  entry     jsonb not null,
  saved_at  timestamptz not null default now(),
  primary key (user_id, key)
);

-- A saved search: the filters the page had set, named by the person. `alerts`
-- is whether a scan that finds a matching card should email them.
create table if not exists public.saved_searches (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  name       text not null,
  filters    jsonb not null,
  alerts     boolean not null default true,
  created_at timestamptz not null default now(),
  last_sent  timestamptz
);

create index if not exists saved_searches_user on public.saved_searches (user_id);

alter table public.profiles       enable row level security;
alter table public.saved_listings enable row level security;
alter table public.saved_searches enable row level security;

-- Each person, their own rows, for every kind of access. No policy exists for
-- anyone else, and no policy means no access.
drop policy if exists "own profile" on public.profiles;
create policy "own profile" on public.profiles
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists "own saved listings" on public.saved_listings;
create policy "own saved listings" on public.saved_listings
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists "own saved searches" on public.saved_searches;
create policy "own saved searches" on public.saved_searches
  for all using (auth.uid() = user_id) with check (auth.uid() = user_id);

-- "Delete my account" from the page. Deleting a user is an admin action in
-- Supabase, so this runs with the function owner's rights, but it can only
-- ever delete the caller: the id comes from their own session, not from a
-- parameter. The cascades above take their rows with them.
create or replace function public.delete_me()
returns void
language sql
security definer
set search_path = public
as $$
  delete from auth.users where id = auth.uid();
$$;

revoke all on function public.delete_me() from public;
grant execute on function public.delete_me() to authenticated;

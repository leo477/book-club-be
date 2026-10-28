-- SCRUM-214: public `covers` bucket for POST /api/v1/upload/cover. Run once in the Supabase SQL editor; idempotent.
-- Intentionally NO insert/update/delete policy on storage.objects: only the service role (RLS bypass) writes.
-- Public read comes from public = true (object URLs are served without a policy).
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('covers', 'covers', true, 5242880, array['image/jpeg', 'image/png', 'image/webp', 'image/gif'])
on conflict (id) do update
set public = excluded.public,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

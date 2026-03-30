-- Execute este SQL no SQL Editor do Supabase

create extension if not exists "pgcrypto";

-- Tabela de usuários
create table if not exists users (
  id         uuid primary key default gen_random_uuid(),
  phone      text not null unique,
  name       text,
  cpf        text unique,
  email      text,
  birth_date date,
  gender     text check (gender in ('male', 'female')),
  created_at timestamptz default now()
);

-- Tabela de conversas (1 ativa por usuário)
create table if not exists conversations (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references users(id) on delete cascade,
  state      text not null default 'GREETING',
  data       jsonb not null default '{}',
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  constraint one_active_per_user unique (user_id)
);

create index if not exists idx_users_phone on users(phone);
create index if not exists idx_conversations_user_id on conversations(user_id);

-- Trigger para atualizar updated_at automaticamente
create or replace function update_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists conversations_updated_at on conversations;
create trigger conversations_updated_at
  before update on conversations
  for each row execute function update_updated_at();

-- Tabela de mensagens (histórico completo)
create table if not exists messages (
  id              uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references conversations(id) on delete cascade,
  role            text not null check (role in ('user', 'assistant')),
  content         text not null,
  created_at      timestamptz default now()
);

create index if not exists idx_messages_conversation_id on messages(conversation_id);
create index if not exists idx_messages_created_at on messages(conversation_id, created_at);

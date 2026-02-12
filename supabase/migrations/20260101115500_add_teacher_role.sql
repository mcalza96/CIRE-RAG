-- Migration: Add Teacher Role to Enum
-- Description: Adds 'teacher' to app_role enum. This must be in a separate file/transaction.

ALTER TYPE public.app_role ADD VALUE IF NOT EXISTS 'teacher';

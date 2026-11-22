ALTER TABLE public_week ADD COLUMN live_preview_id TEXT REFERENCES public_preview(id);
ALTER TABLE public_week ADD COLUMN live_observed_at TEXT;

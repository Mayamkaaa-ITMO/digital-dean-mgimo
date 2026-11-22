ALTER TABLE lab_door ADD COLUMN university_id TEXT NOT NULL DEFAULT 'spbstu-demo';
DROP INDEX lab_door_room;
CREATE INDEX lab_door_scope ON lab_door(university_id,room,id);

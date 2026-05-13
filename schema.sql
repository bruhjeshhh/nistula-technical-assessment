
-- Nistula Messaging Platform — Simplified Schema
-- ============================================================


-- AGENTS
-- Hotel staff who send and review messages.
CREATE TABLE agents (
    id         SERIAL PRIMARY KEY,
    name       VARCHAR(100) NOT NULL,
    email      VARCHAR(150) NOT NULL UNIQUE,
    role       VARCHAR(50)  NOT NULL DEFAULT 'agent', -- 'agent', 'supervisor', 'admin'
    is_active  BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP    NOT NULL DEFAULT NOW()
);


-- GUESTS
-- One record per guest, shared across all channels
-- channel_handle is how we reach them (phone, email, etc.)
-- channel_type tells us which platform to use
CREATE TABLE guests (
    id             SERIAL PRIMARY KEY,
    full_name      VARCHAR(150),
    email          VARCHAR(150),
    phone          VARCHAR(30),
    channel_type   VARCHAR(50) NOT NULL, -- 'whatsapp', 'sms', 'email', 'in_app'
    channel_handle VARCHAR(150) NOT NULL, -- phone number, email address, user ID
    language       VARCHAR(10)  NOT NULL DEFAULT 'en',
    created_at     TIMESTAMP    NOT NULL DEFAULT NOW(),
    UNIQUE (channel_type, channel_handle)
);

CREATE INDEX idx_guests_email  ON guests (email);
CREATE INDEX idx_guests_phone  ON guests (phone);


-- RESERVATIONS
-- Mirrors the key fields from the property management system
CREATE TABLE reservations (
    id                 SERIAL PRIMARY KEY,
    pms_reservation_id VARCHAR(100) NOT NULL UNIQUE, -- ID from the PMS
    guest_id           INT          NOT NULL REFERENCES guests (id),
    property_code      VARCHAR(50)  NOT NULL,
    checkin_date       DATE         NOT NULL,
    checkout_date      DATE         NOT NULL,
    status             VARCHAR(50)  NOT NULL DEFAULT 'confirmed',  -- 'confirmed', 'checked_in', 'checked_out', 'cancelled'
    created_at         TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_reservations_guest ON reservations (guest_id);


-- CONVERSATIONS
-- A thread of messages between the hotel and one guest.
-- Linked to both the guest and their reservation.
-- reservation_id is nullable for pre-booking inquiries.
CREATE TABLE conversations (
    id                SERIAL PRIMARY KEY,
    guest_id          INT         NOT NULL REFERENCES guests (id),
    reservation_id    INT                  REFERENCES reservations (id),
    assigned_agent_id INT                  REFERENCES agents (id),
    status            VARCHAR(50) NOT NULL DEFAULT 'open',
        -- 'open', 'pending', 'resolved'
    topic             VARCHAR(150),        -- e.g. 'late_checkout', 'noise_complaint'
    opened_at         TIMESTAMP   NOT NULL DEFAULT NOW(),
    resolved_at       TIMESTAMP            -- NULL while still open
);

CREATE INDEX idx_conversations_guest  ON conversations (guest_id);
CREATE INDEX idx_conversations_res    ON conversations (reservation_id);
CREATE INDEX idx_conversations_status ON conversations (status);



-- MESSAGES
-- Every message across all channels in one table.
-- AI tracking columns:
--         ai_drafted   — the AI wrote a draft for this message
--         agent_edited — an agent changed the draft before sending
--         auto_sent    — sent automatically without agent review
--
-- ai_confidence and ai_query_type are filled in for inbound
-- messages that the AI has analyzed.
CREATE TABLE messages (
    id                  SERIAL PRIMARY KEY,
    conversation_id     INT          NOT NULL REFERENCES conversations (id),
    direction           VARCHAR(10)  NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    body                TEXT,
    content_type        VARCHAR(50)  NOT NULL DEFAULT 'text',
        -- 'text', 'image', 'audio', 'document'

    -- AI authorship tracking
    ai_drafted          BOOLEAN      NOT NULL DEFAULT FALSE,
    agent_edited        BOOLEAN      NOT NULL DEFAULT FALSE,
    auto_sent           BOOLEAN      NOT NULL DEFAULT FALSE,
    drafted_by_agent_id INT                   REFERENCES agents (id),

    -- AI analysis (inbound messages only)
    ai_query_type       VARCHAR(100),
        -- 'check_in_query', 'complaint', 'amenity_request', 'general_inquiry', etc.
    ai_confidence       NUMERIC(4,3) CHECK (ai_confidence BETWEEN 0 AND 1),

    sent_at             TIMESTAMP,
    created_at          TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_messages_conversation ON messages (conversation_id, created_at DESC);
CREATE INDEX idx_messages_direction    ON messages (direction);
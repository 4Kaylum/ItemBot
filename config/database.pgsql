CREATE TABLE guild_settings(
    guild_id BIGINT PRIMARY KEY,
    prefix VARCHAR(30)
);


CREATE TABLE user_inventories(
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    item_name VARCHAR(200) NOT NULL,
    amount INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id, item_name)
);


CREATE TABLE guild_items(
    guild_id BIGINT NOT NULL,
    item_name VARCHAR(200) NOT NULL,
    PRIMARY KEY (guild_id, item_name)
);


CREATE TYPE acquire_type AS ENUM(
    'Message', 'Command'
);


CREATE TABLE guild_item_acquire_methods(
    guild_id BIGINT NOT NULL,
    item_name VARCHAR(200) NOT NULL,
    acquired_by acquire_type NOT NULL,
    min_acquired INTEGER,
    max_acquired INTEGER,
    acquire_per INTEGER,
    PRIMARY KEY (guild_id, item_name, acquired_by)
);


CREATE TABLE guild_item_shop_messages(
    guild_id BIGINT NOT NULL,
    item_name VARCHAR(200) NOT NULL,
    message_id BIGINT NOT NULL,
    amount_gained INTEGER NOT NULL DEFAULT 1,
    item_required VARCHAR(200) NOT NULL,
    required_item_amount INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (guild_id, item_name, message_id)
);

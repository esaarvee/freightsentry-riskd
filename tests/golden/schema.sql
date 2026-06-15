    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);
    ADD CONSTRAINT api_tokens_pkey PRIMARY KEY (id);
    ADD CONSTRAINT api_tokens_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);
    ADD CONSTRAINT app_users_pkey PRIMARY KEY (id);
    ADD CONSTRAINT app_users_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);
    ADD CONSTRAINT customer_baselines_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.customers(id);
    ADD CONSTRAINT customer_baselines_pkey PRIMARY KEY (id);
    ADD CONSTRAINT customer_baselines_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);
    ADD CONSTRAINT customers_enterprise_id_fkey FOREIGN KEY (enterprise_id) REFERENCES public.enterprises(id);
    ADD CONSTRAINT customers_pkey PRIMARY KEY (id);
    ADD CONSTRAINT customers_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);
    ADD CONSTRAINT decisions_pkey PRIMARY KEY (id);
    ADD CONSTRAINT decisions_shipment_id_fkey FOREIGN KEY (tenant_id, shipment_id) REFERENCES public.shipments(tenant_id, id);
    ADD CONSTRAINT decisions_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);
    ADD CONSTRAINT enterprises_pkey PRIMARY KEY (id);
    ADD CONSTRAINT enterprises_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);
    ADD CONSTRAINT feedback_pkey PRIMARY KEY (id);
    ADD CONSTRAINT feedback_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);
    ADD CONSTRAINT global_blocked_vectors_created_by_tenant_id_fkey FOREIGN KEY (created_by_tenant_id) REFERENCES public.tenants(id);
    ADD CONSTRAINT global_blocked_vectors_pkey PRIMARY KEY (id);
    ADD CONSTRAINT ip_enrichment_pkey PRIMARY KEY (ip);
    ADD CONSTRAINT shipments_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.customers(id);
    ADD CONSTRAINT shipments_pkey PRIMARY KEY (tenant_id, id);
    ADD CONSTRAINT shipments_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);
    ADD CONSTRAINT shipments_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id);
    ADD CONSTRAINT tenant_route_baselines_pkey PRIMARY KEY (tenant_id, customer_country, origin_country, destination_country);
    ADD CONSTRAINT tenant_route_baselines_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);
    ADD CONSTRAINT tenants_pkey PRIMARY KEY (id);
    ADD CONSTRAINT users_customer_id_fkey FOREIGN KEY (customer_id) REFERENCES public.customers(id);
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);
    ADD CONSTRAINT users_tenant_id_fkey FOREIGN KEY (tenant_id) REFERENCES public.tenants(id);
    ADD CONSTRAINT ux_api_tokens_token_hash UNIQUE (token_hash);
    ADD CONSTRAINT ux_app_users_tenant_external UNIQUE (tenant_id, external_id);
    ADD CONSTRAINT ux_customer_baselines_tenant_customer UNIQUE (tenant_id, customer_id);
    ADD CONSTRAINT ux_customers_tenant_external UNIQUE (tenant_id, external_id);
    ADD CONSTRAINT ux_enterprises_tenant_external UNIQUE (tenant_id, external_id);
    ADD CONSTRAINT ux_feedback_tenant_request UNIQUE (tenant_id, request_id);
    ADD CONSTRAINT ux_global_blocked_vectors_type_hash UNIQUE (vector_type, vector_hash);
    ADD CONSTRAINT ux_shipments_tenant_request UNIQUE (tenant_id, request_id);
    ADD CONSTRAINT ux_users_tenant_customer_external UNIQUE (tenant_id, customer_id, external_id);
    AS integer
    AS integer
    AS integer
    AS integer
    AS integer
    AS integer
    AS integer
    AS integer
    AS integer
    AS integer
    CACHE 1;
    CACHE 1;
    CACHE 1;
    CACHE 1;
    CACHE 1;
    CACHE 1;
    CACHE 1;
    CACHE 1;
    CACHE 1;
    CACHE 1;
    CONSTRAINT ck_decisions_request_type CHECK ((request_type = ANY (ARRAY['booking'::text, 'modification'::text])))
    CONSTRAINT ck_feedback_label CHECK ((label = ANY (ARRAY['approved'::text, 'rejected'::text, 'fraud_confirmed'::text])))
    INCREMENT BY 1
    INCREMENT BY 1
    INCREMENT BY 1
    INCREMENT BY 1
    INCREMENT BY 1
    INCREMENT BY 1
    INCREMENT BY 1
    INCREMENT BY 1
    INCREMENT BY 1
    INCREMENT BY 1
    NO MAXVALUE
    NO MAXVALUE
    NO MAXVALUE
    NO MAXVALUE
    NO MAXVALUE
    NO MAXVALUE
    NO MAXVALUE
    NO MAXVALUE
    NO MAXVALUE
    NO MAXVALUE
    NO MINVALUE
    NO MINVALUE
    NO MINVALUE
    NO MINVALUE
    NO MINVALUE
    NO MINVALUE
    NO MINVALUE
    NO MINVALUE
    NO MINVALUE
    NO MINVALUE
    START WITH 1
    START WITH 1
    START WITH 1
    START WITH 1
    START WITH 1
    START WITH 1
    START WITH 1
    START WITH 1
    START WITH 1
    START WITH 1
    asn_org text,
    booking_ts timestamp with time zone NOT NULL,
    business_name text,
    cadence_m2_h numeric DEFAULT 0 NOT NULL,
    cadence_mean_h numeric DEFAULT 0 NOT NULL,
    cadence_n numeric DEFAULT 0 NOT NULL,
    channel text NOT NULL,
    channel_hist jsonb DEFAULT '{}'::jsonb NOT NULL,
    city text,
    classification text NOT NULL,
    cloud_provider text,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    country text,
    country_route_stats jsonb DEFAULT '{}'::jsonb NOT NULL
    country_stats jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
    created_at timestamp with time zone DEFAULT now() NOT NULL
    created_at timestamp with time zone DEFAULT now() NOT NULL
    created_at timestamp with time zone DEFAULT now() NOT NULL
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    created_by_tenant_id integer NOT NULL,
    customer_country character varying(2) NOT NULL,
    customer_id integer NOT NULL,
    customer_id integer NOT NULL,
    customer_id integer NOT NULL,
    decay_anchor_date date,
    decision text NOT NULL,
    dest_stats jsonb DEFAULT '{}'::jsonb NOT NULL,
    destination jsonb NOT NULL,
    destination_country character varying(2) NOT NULL,
    destination_hmac text NOT NULL,
    email_domain_stats jsonb DEFAULT '{}'::jsonb NOT NULL,
    email_hmac text,
    email_hmacs jsonb DEFAULT '{}'::jsonb NOT NULL,
    enterprise_id integer,
    external_id text NOT NULL,
    external_id text NOT NULL,
    external_id text NOT NULL,
    external_id text NOT NULL,
    feedback_ts timestamp with time zone NOT NULL,
    fh_level1 boolean DEFAULT false NOT NULL,
    fh_level2 boolean DEFAULT false NOT NULL,
    fh_lists text,
    first_seen timestamp with time zone DEFAULT now() NOT NULL,
    first_seen timestamp with time zone DEFAULT now() NOT NULL,
    first_seen timestamp with time zone DEFAULT now() NOT NULL,
    first_seen timestamp with time zone DEFAULT now() NOT NULL,
    first_seen timestamp with time zone DEFAULT now() NOT NULL,
    flagged_count integer DEFAULT 0 NOT NULL,
    fraud_confirmed_count integer DEFAULT 0 NOT NULL,
    hour_hist jsonb DEFAULT '{}'::jsonb NOT NULL,
    id integer NOT NULL,
    id integer NOT NULL,
    id integer NOT NULL,
    id integer NOT NULL,
    id integer NOT NULL,
    id integer NOT NULL,
    id integer NOT NULL,
    id integer NOT NULL,
    id integer NOT NULL,
    id integer NOT NULL,
    id text NOT NULL,
    ip inet NOT NULL,
    ip_asn_stats jsonb DEFAULT '{}'::jsonb NOT NULL,
    ip_netblock_stats jsonb DEFAULT '{}'::jsonb NOT NULL,
    ip_stats jsonb DEFAULT '{}'::jsonb NOT NULL,
    ip_type_hist jsonb DEFAULT '{}'::jsonb NOT NULL,
    is_api_partner boolean DEFAULT false NOT NULL,
    is_cloud boolean DEFAULT false NOT NULL,
    is_datacenter boolean DEFAULT false NOT NULL,
    is_proxy boolean DEFAULT false NOT NULL,
    is_tor boolean DEFAULT false NOT NULL,
    is_vpn boolean DEFAULT false NOT NULL,
    label text NOT NULL,
    lane_stats jsonb DEFAULT '{}'::jsonb NOT NULL,
    last_booking_country text,
    last_booking_lat numeric(8,5),
    last_booking_lon numeric(8,5),
    last_booking_ts timestamp with time zone,
    last_seen timestamp with time zone DEFAULT now() NOT NULL,
    last_seen timestamp with time zone DEFAULT now() NOT NULL,
    last_seen timestamp with time zone DEFAULT now() NOT NULL,
    last_updated timestamp with time zone DEFAULT now() NOT NULL
    last_used_at timestamp with time zone
    lat numeric(8,5),
    lon numeric(8,5),
    name text NOT NULL,
    note text,
    observation_count bigint DEFAULT 0 NOT NULL,
    operator_id text,
    origin jsonb NOT NULL,
    origin_country character varying(2) NOT NULL,
    origin_ip_country_stats jsonb DEFAULT '{}'::jsonb NOT NULL,
    origin_stats jsonb DEFAULT '{}'::jsonb NOT NULL,
    phone_hmac text,
    phone_hmacs jsonb DEFAULT '{}'::jsonb NOT NULL,
    phone_prefix_stats jsonb DEFAULT '{}'::jsonb NOT NULL,
    proxy_type text,
    region text,
    registered_address text,
    registered_country character varying(2)
    rejected_email_hmacs jsonb DEFAULT '{}'::jsonb NOT NULL,
    rejected_phone_hmacs jsonb DEFAULT '{}'::jsonb NOT NULL,
    request_id text NOT NULL,
    request_id text NOT NULL,
    request_id text NOT NULL,
    request_type text DEFAULT 'booking'::text NOT NULL,
    risk_factors jsonb DEFAULT '[]'::jsonb NOT NULL,
    risk_level text NOT NULL,
    role text DEFAULT 'tenant'::text NOT NULL,
    role text NOT NULL,
    score numeric(5,4) NOT NULL,
    share_enabled boolean DEFAULT false NOT NULL,
    shipment_id text NOT NULL,
    source_ip inet NOT NULL,
    target_request_id text NOT NULL,
    tenant_id integer NOT NULL,
    tenant_id integer NOT NULL,
    tenant_id integer NOT NULL,
    tenant_id integer NOT NULL,
    tenant_id integer NOT NULL,
    tenant_id integer NOT NULL,
    tenant_id integer NOT NULL,
    tenant_id integer NOT NULL,
    tenant_id integer NOT NULL,
    tenant_id integer NOT NULL,
    threat text,
    token_hash text NOT NULL,
    total_shipments integer DEFAULT 0 NOT NULL,
    transaction_number text NOT NULL
    triggered_rules text[] DEFAULT '{}'::text[] NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
    updated_at timestamp with time zone DEFAULT now() NOT NULL
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    user_id integer NOT NULL,
    value numeric(14,2) NOT NULL,
    value_m2 numeric DEFAULT 0 NOT NULL,
    value_mean numeric DEFAULT 0 NOT NULL,
    value_n numeric DEFAULT 0 NOT NULL,
    vector_hash text NOT NULL,
    vector_type text NOT NULL,
    version_num character varying(32) NOT NULL
    weekday_hist jsonb DEFAULT '{}'::jsonb NOT NULL,
);
);
);
);
);
);
);
);
);
);
);
);
);
);
ALTER SEQUENCE public.api_tokens_id_seq OWNED BY public.api_tokens.id;
ALTER SEQUENCE public.app_users_id_seq OWNED BY public.app_users.id;
ALTER SEQUENCE public.customer_baselines_id_seq OWNED BY public.customer_baselines.id;
ALTER SEQUENCE public.customers_id_seq OWNED BY public.customers.id;
ALTER SEQUENCE public.decisions_id_seq OWNED BY public.decisions.id;
ALTER SEQUENCE public.enterprises_id_seq OWNED BY public.enterprises.id;
ALTER SEQUENCE public.feedback_id_seq OWNED BY public.feedback.id;
ALTER SEQUENCE public.global_blocked_vectors_id_seq OWNED BY public.global_blocked_vectors.id;
ALTER SEQUENCE public.tenants_id_seq OWNED BY public.tenants.id;
ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;
ALTER TABLE ONLY public.alembic_version
ALTER TABLE ONLY public.api_tokens
ALTER TABLE ONLY public.api_tokens
ALTER TABLE ONLY public.api_tokens
ALTER TABLE ONLY public.api_tokens ALTER COLUMN id SET DEFAULT nextval('public.api_tokens_id_seq'::regclass);
ALTER TABLE ONLY public.app_users
ALTER TABLE ONLY public.app_users
ALTER TABLE ONLY public.app_users
ALTER TABLE ONLY public.app_users ALTER COLUMN id SET DEFAULT nextval('public.app_users_id_seq'::regclass);
ALTER TABLE ONLY public.customer_baselines
ALTER TABLE ONLY public.customer_baselines
ALTER TABLE ONLY public.customer_baselines
ALTER TABLE ONLY public.customer_baselines
ALTER TABLE ONLY public.customer_baselines ALTER COLUMN id SET DEFAULT nextval('public.customer_baselines_id_seq'::regclass);
ALTER TABLE ONLY public.customers
ALTER TABLE ONLY public.customers
ALTER TABLE ONLY public.customers
ALTER TABLE ONLY public.customers
ALTER TABLE ONLY public.customers ALTER COLUMN id SET DEFAULT nextval('public.customers_id_seq'::regclass);
ALTER TABLE ONLY public.decisions
ALTER TABLE ONLY public.decisions
ALTER TABLE ONLY public.decisions
ALTER TABLE ONLY public.decisions ALTER COLUMN id SET DEFAULT nextval('public.decisions_id_seq'::regclass);
ALTER TABLE ONLY public.enterprises
ALTER TABLE ONLY public.enterprises
ALTER TABLE ONLY public.enterprises
ALTER TABLE ONLY public.enterprises ALTER COLUMN id SET DEFAULT nextval('public.enterprises_id_seq'::regclass);
ALTER TABLE ONLY public.feedback
ALTER TABLE ONLY public.feedback
ALTER TABLE ONLY public.feedback
ALTER TABLE ONLY public.feedback ALTER COLUMN id SET DEFAULT nextval('public.feedback_id_seq'::regclass);
ALTER TABLE ONLY public.global_blocked_vectors
ALTER TABLE ONLY public.global_blocked_vectors
ALTER TABLE ONLY public.global_blocked_vectors
ALTER TABLE ONLY public.global_blocked_vectors ALTER COLUMN id SET DEFAULT nextval('public.global_blocked_vectors_id_seq'::regclass);
ALTER TABLE ONLY public.ip_enrichment
ALTER TABLE ONLY public.shipments
ALTER TABLE ONLY public.shipments
ALTER TABLE ONLY public.shipments
ALTER TABLE ONLY public.shipments
ALTER TABLE ONLY public.shipments
ALTER TABLE ONLY public.tenant_route_baselines
ALTER TABLE ONLY public.tenant_route_baselines
ALTER TABLE ONLY public.tenants
ALTER TABLE ONLY public.tenants ALTER COLUMN id SET DEFAULT nextval('public.tenants_id_seq'::regclass);
ALTER TABLE ONLY public.users
ALTER TABLE ONLY public.users
ALTER TABLE ONLY public.users
ALTER TABLE ONLY public.users
ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);
ALTER TABLE public.customer_baselines ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.enterprises ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.feedback ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.shipments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tenant_route_baselines ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;
CREATE INDEX ix_api_tokens_tenant ON public.api_tokens USING btree (tenant_id);
CREATE INDEX ix_api_tokens_tenant_last_used ON public.api_tokens USING btree (tenant_id, last_used_at DESC NULLS LAST);
CREATE INDEX ix_app_users_tenant ON public.app_users USING btree (tenant_id);
CREATE INDEX ix_customers_tenant_id ON public.customers USING btree (tenant_id);
CREATE INDEX ix_decisions_tenant_request_type_created ON public.decisions USING btree (tenant_id, request_type, created_at);
CREATE INDEX ix_decisions_tenant_shipment ON public.decisions USING btree (tenant_id, shipment_id);
CREATE INDEX ix_enterprises_tenant_id ON public.enterprises USING btree (tenant_id);
CREATE INDEX ix_feedback_tenant_target ON public.feedback USING btree (tenant_id, target_request_id);
CREATE INDEX ix_shipments_tenant_customer_booking_ts ON public.shipments USING btree (tenant_id, customer_id, booking_ts);
CREATE INDEX ix_shipments_tenant_dest_hmac_booking_ts ON public.shipments USING btree (tenant_id, destination_hmac, booking_ts);
CREATE INDEX ix_shipments_tenant_ip_booking_ts ON public.shipments USING btree (tenant_id, source_ip, booking_ts);
CREATE POLICY tenant_isolation ON public.customer_baselines USING ((tenant_id = (current_setting('app.tenant_id'::text))::integer));
CREATE POLICY tenant_isolation ON public.customers USING ((tenant_id = (current_setting('app.tenant_id'::text))::integer));
CREATE POLICY tenant_isolation ON public.decisions USING ((tenant_id = (current_setting('app.tenant_id'::text))::integer));
CREATE POLICY tenant_isolation ON public.enterprises USING ((tenant_id = (current_setting('app.tenant_id'::text))::integer));
CREATE POLICY tenant_isolation ON public.feedback USING ((tenant_id = (current_setting('app.tenant_id'::text))::integer));
CREATE POLICY tenant_isolation ON public.shipments USING ((tenant_id = (current_setting('app.tenant_id'::text))::integer));
CREATE POLICY tenant_isolation ON public.tenant_route_baselines USING ((tenant_id = (current_setting('app.tenant_id'::text))::integer));
CREATE POLICY tenant_isolation ON public.users USING ((tenant_id = (current_setting('app.tenant_id'::text))::integer));
CREATE SEQUENCE public.api_tokens_id_seq
CREATE SEQUENCE public.app_users_id_seq
CREATE SEQUENCE public.customer_baselines_id_seq
CREATE SEQUENCE public.customers_id_seq
CREATE SEQUENCE public.decisions_id_seq
CREATE SEQUENCE public.enterprises_id_seq
CREATE SEQUENCE public.feedback_id_seq
CREATE SEQUENCE public.global_blocked_vectors_id_seq
CREATE SEQUENCE public.tenants_id_seq
CREATE SEQUENCE public.users_id_seq
CREATE TABLE public.alembic_version (
CREATE TABLE public.api_tokens (
CREATE TABLE public.app_users (
CREATE TABLE public.customer_baselines (
CREATE TABLE public.customers (
CREATE TABLE public.decisions (
CREATE TABLE public.enterprises (
CREATE TABLE public.feedback (
CREATE TABLE public.global_blocked_vectors (
CREATE TABLE public.ip_enrichment (
CREATE TABLE public.shipments (
CREATE TABLE public.tenant_route_baselines (
CREATE TABLE public.tenants (
CREATE TABLE public.users (
CREATE UNIQUE INDEX ux_decisions_tenant_request_type ON public.decisions USING btree (tenant_id, request_type, request_id);
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.alembic_version TO riskd_app;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.api_tokens TO riskd_app;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.app_users TO riskd_app;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.customer_baselines TO riskd_app;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.customers TO riskd_app;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.decisions TO riskd_app;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.enterprises TO riskd_app;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.feedback TO riskd_app;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.global_blocked_vectors TO riskd_app;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.ip_enrichment TO riskd_app;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.shipments TO riskd_app;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.tenant_route_baselines TO riskd_app;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.tenants TO riskd_app;
GRANT SELECT,INSERT,DELETE,UPDATE ON TABLE public.users TO riskd_app;
GRANT SELECT,USAGE ON SEQUENCE public.api_tokens_id_seq TO riskd_app;
GRANT SELECT,USAGE ON SEQUENCE public.app_users_id_seq TO riskd_app;
GRANT SELECT,USAGE ON SEQUENCE public.customer_baselines_id_seq TO riskd_app;
GRANT SELECT,USAGE ON SEQUENCE public.customers_id_seq TO riskd_app;
GRANT SELECT,USAGE ON SEQUENCE public.decisions_id_seq TO riskd_app;
GRANT SELECT,USAGE ON SEQUENCE public.enterprises_id_seq TO riskd_app;
GRANT SELECT,USAGE ON SEQUENCE public.feedback_id_seq TO riskd_app;
GRANT SELECT,USAGE ON SEQUENCE public.global_blocked_vectors_id_seq TO riskd_app;
GRANT SELECT,USAGE ON SEQUENCE public.tenants_id_seq TO riskd_app;
GRANT SELECT,USAGE ON SEQUENCE public.users_id_seq TO riskd_app;
GRANT USAGE ON SCHEMA public TO riskd_app;
SELECT pg_catalog.set_config('search_path', '', false);

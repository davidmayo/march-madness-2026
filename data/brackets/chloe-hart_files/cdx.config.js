if (typeof window !== 'undefined') {
    // set CDX categories for known third-party entities
    window.MML_CDX = window.MML_CDX || {
        adform: ['ads-contextual', 'ads-person', 'ads-person-prof', 'measure-ads', 'vendor'],
        adobe: ['content-person', 'measure-content'],
        akamai: ['measure-content', 'measure-market', 'product-develop'],
        chartbeart: ['measure-content', 'measure-market', 'product-develop'],
        cloud_front: ['measure-content', 'measure-market', 'product-develop'],
        comscore: ['measure-content', 'vendor'],
        facebook_pixel: ['ads-contextual', 'ads-person', 'ads-person-prof', 'data-store', 'vendor'],
        floodlight: ['data-store', 'vendor'],
        gtm: ['ads-contextual', 'ads-person', 'ads-person-prof', 'data-store', 'measure-ads', 'vendor'],
        insight_pixel: ['ads-contextual', 'ads-person', 'measure-ads'],
        neilsen: ['data-store', 'measure-content', 'vendor'],
        new_relic: ['measure-content', 'measure-market', 'product-develop'],
        quantcast: ['vendor'],
        simpli_fi: ['ads-contextual', 'ads-person', 'measure-ads', 'vendor'],
        twitter_pixel: ['ads-contextual', 'ads-person', 'ads-person-prof', 'content-person', 'content-person-prof', 'data-store', 'vendor'],
        wunderkind: ['measure-content', 'measure-market', 'product-develop'],
    };
    // Casablanca exposes drupalSettings.cdx for the analytics libraries so we
    // mimic it here so it can be used by the telemetry team. @see Peter Kim
    window.drupalSettings = window.drupalSettings || {};
    window.drupalSettings.cdx = window.drupalSettings.cdx || window.MML_CDX;
    if (typeof window.WBD.UserConsent !== 'undefined') {
        const SEO_BOT_PATTERN = /Googlebot|bingbot|DuckDuckBot|Baiduspider|YandexBot|facebookexternalhit|Twitterbot|LinkedInBot|WhatsApp|Slackbot|Applebot|Chrome-Lighthouse/i;
        const AI_BOT_PATTERN = /GPTBot|PerplexityBot|ClaudeBot|Google-Extended|CCBot|anthropic-ai|Bytespider|Diffbot|ImagesiftBot|Omgilibot/i;
        const ua = navigator.userAgent || '';
        const botState = AI_BOT_PATTERN.test(ua) ? 32 : SEO_BOT_PATTERN.test(ua) ? 8 : 0;

        window.WBD.UserConsent.init({
            cookieDomain: '.ncaa.com',
            domId: '27c817ca-399c-4851-9dc1-5725121225d3',
            src: 'https://cdn.cookielaw.org/scripttemplates/otSDKStub.js',
            cookieSameSite: 'None',
            cookieSecure: true,
            ackTermsCookie: 'wbdLTP',
            ackTermsEnforce: true,
            botState: botState,
        });
    } else {
        console.warn('Could not initialize due to missing UserConsent script.');
    }
}

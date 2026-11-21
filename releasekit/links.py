"""Sign-up and registration directory shown in the 'Sign-ups' tab.

Each entry: name, url, what it does, what to have ready, and when to do it.
Links point at each service's own sign-up / join page. Edit freely: this file is
plain data and the UI reads it on every load.
"""

DIRECTORY = [
    {
        "group": "1. Distributors (gets you onto Spotify, Apple Music, Amazon, YouTube Music, TikTok, Deezer, Tidal...)",
        "note": "Spotify, Apple Music and Amazon have no direct upload for independent artists; you choose ONE distributor per release and it delivers to all the stores. Pricing models differ (yearly fee vs per-release vs revenue share) and change often, so compare on their sites.",
        "items": [
            {"name": "DistroKid", "url": "https://distrokid.com", "what": "Yearly subscription, unlimited uploads.", "ready": "Email, payment method, WAV + 3000px cover (Distributor package)."},
            {"name": "TuneCore", "url": "https://www.tunecore.com", "what": "Distributor with free and paid tiers, plus publishing admin.", "ready": "Distributor package."},
            {"name": "CD Baby", "url": "https://cdbaby.com", "what": "Pay per release; releases stay up without a yearly bill. Also physical CD/vinyl.", "ready": "Distributor package."},
            {"name": "Ditto Music", "url": "https://dittomusic.com", "what": "Yearly subscription distributor.", "ready": "Distributor package."},
            {"name": "Amuse", "url": "https://www.amuse.io", "what": "Free tier with limits, paid tiers for more stores and features.", "ready": "Distributor package."},
            {"name": "RouteNote", "url": "https://routenote.com", "what": "Free tier (revenue share) or premium tier (keep 100%).", "ready": "Distributor package."},
            {"name": "UnitedMasters", "url": "https://unitedmasters.com", "what": "Distributor with brand-partnership programs.", "ready": "Distributor package."},
            {"name": "SoundCloud for Artists", "url": "https://soundcloud.com/getstarted/pricing", "what": "SoundCloud hosting plus distribution to other stores on paid Artist plans.", "ready": "Account; Bandcamp/SoundCloud package."},
        ],
    },
    {
        "group": "2. Claim your artist profiles (after your first release is live)",
        "note": "These need a release already live on that store. Do them in the first week after release day.",
        "items": [
            {"name": "Spotify for Artists", "url": "https://artists.spotify.com", "what": "Profile, stats, editorial pitching (pitch at least 7 days before release), Canvas uploads.", "ready": "Spotify account; link to your artist page or an upcoming release from your distributor."},
            {"name": "Apple Music for Artists", "url": "https://artists.apple.com", "what": "Stats including Shazam, profile image, Motion Art.", "ready": "Apple Account; your Apple Music artist link."},
            {"name": "Amazon Music for Artists", "url": "https://artists.amazon.com", "what": "Stats including Alexa plays, profile image.", "ready": "Amazon account."},
            {"name": "YouTube Studio / Official Artist Channel", "url": "https://studio.youtube.com", "what": "Your channel. Your distributor can request an Official Artist Channel that merges your Topic channel.", "ready": "Google account; 3+ releases distributed is the usual OAC requirement, ask your distributor."},
            {"name": "Deezer for Creators", "url": "https://creators.deezer.com", "what": "Profile and stats on Deezer.", "ready": "Deezer account."},
            {"name": "Pandora AMP", "url": "https://amp.pandora.com", "what": "Pandora artist stats and Artist Audio Messages (US).", "ready": "Pandora account."},
        ],
    },
    {
        "group": "3. Sell direct to fans",
        "note": "You upload these yourself; use the Bandcamp/SoundCloud package.",
        "items": [
            {"name": "Bandcamp (artist account)", "url": "https://bandcamp.com/artists", "what": "Sell downloads, vinyl, merch; you set the price.", "ready": "WAV/FLAC files, 1400px+ cover, description and credits from the package."},
            {"name": "SoundCloud", "url": "https://soundcloud.com/getstarted", "what": "Free hosting and community; paid plans add monetization.", "ready": "MP3/WAV, cover, description."},
        ],
    },
    {
        "group": "4. Stock & production music libraries (license your tracks to video makers)",
        "note": "Read each contract. Some are non-exclusive (you can sell the same track elsewhere); some buy all rights outright. Most refuse fully AI-generated music. Use the Stock package.",
        "items": [
            {"name": "Pond5 (Sell Your Media)", "url": "https://www.pond5.com/sell-your-media", "what": "Open marketplace: free account, ID verification, non-exclusive WAV uploads. Also accepts photos and footage.", "ready": "ID document, WAV files, keywords CSV from the Stock package."},
            {"name": "Storyblocks contributor", "url": "https://contribute-faq.storyblocks.com/en/articles/16562766-how-to-sell-your-music-on-storyblocks", "what": "Apply first; WAV/AIFF tracks at least 60 s long.", "ready": "Application, WAV files."},
            {"name": "Shutterstock Music", "url": "https://submit.shutterstock.com/help/en/articles/10594590-how-do-i-become-a-music-contributor", "what": "Very selective: submit your best 5 tracks once, MP3 up to 15 MB. Contract is a full transfer of rights for an upfront payment.", "ready": "5 MP3s from the Stock package (shutterstock_mp3 folder)."},
            {"name": "PremiumBeat (Shutterstock) new artist", "url": "https://www.premiumbeat.com/new-artist", "what": "Curated library; review takes a few weeks.", "ready": "Your strongest instrumentals."},
            {"name": "Artlist artist application", "url": "https://artlist.io/Home/Application/", "what": "Curated subscription library; apply with samples.", "ready": "Portfolio links."},
        ],
    },
    {
        "group": "5. Stock photo sites (sell your photos, art and covers)",
        "note": "Use the Photo stock package: resized JPGs plus a keyword CSV.",
        "items": [
            {"name": "Shutterstock contributor", "url": "https://submit.shutterstock.com", "what": "Photos, illustrations and footage.", "ready": "ID, JPGs at least 4 MP, CSV metadata."},
            {"name": "Adobe Stock contributor", "url": "https://contributor.stock.adobe.com", "what": "Photos, illustrations, video (Adobe Stock does not take music from contributors).", "ready": "Adobe account, JPGs, CSV."},
            {"name": "Pond5 (photos & footage)", "url": "https://www.pond5.com/sell-your-media", "what": "Same contributor account as music.", "ready": "JPGs, CSV."},
        ],
    },
    {
        "group": "6. Get paid for the songwriting & recording (do these before release)",
        "note": "Streaming pays several separate royalties. Your distributor collects the master/streaming money; these collect the rest. Rules shown are for the US; other countries have their own societies.",
        "items": [
            {"name": "ASCAP (join)", "url": "https://www.ascap.com", "what": "Performance rights org (PRO): collects songwriter performance royalties. Join ONE PRO: ASCAP or BMI.", "ready": "Legal name, tax ID, song titles and splits."},
            {"name": "BMI (join)", "url": "https://www.bmi.com", "what": "The other main US PRO. Pick ASCAP or BMI, not both.", "ready": "Same as above."},
            {"name": "The MLC (membership)", "url": "https://www.themlc.com/membership", "what": "Collects US digital mechanical royalties from streaming; the only way to get them is to register your songs here. Free.", "ready": "Songs, writers, shares, ISRCs (in metadata.csv)."},
            {"name": "SoundExchange", "url": "https://www.soundexchange.com", "what": "Collects digital performance royalties for the recording (Pandora, SiriusXM, web radio). Register as performer and rights owner.", "ready": "ISRCs, release info."},
            {"name": "US Copyright Office", "url": "https://www.copyright.gov/registration/", "what": "Formal registration of songs/recordings (needed to sue for infringement in the US).", "ready": "Audio files, lyrics, authorship info."},
            {"name": "US ISRC agency (usisrc.org)", "url": "https://usisrc.org", "what": "Get your own ISRC prefix if you don't want your distributor's codes.", "ready": "Optional - distributors give free ISRCs."},
        ],
    },
    {
        "group": "7. YouTube auto-upload setup (for the Upload tab)",
        "note": "Free, one-time setup. New, unaudited API projects can only upload as Private; you then flip videos to Public in YouTube Studio, or apply for an audit.",
        "items": [
            {"name": "Enable YouTube Data API v3", "url": "https://console.cloud.google.com/apis/library/youtube.googleapis.com", "what": "Create a Google Cloud project and enable the API.", "ready": "Google account."},
            {"name": "Create OAuth client (Desktop app)", "url": "https://console.cloud.google.com/apis/credentials", "what": "Create an OAuth client ID of type Desktop and download the JSON as client_secret.json.", "ready": "Add yourself as a test user on the OAuth consent screen."},
            {"name": "Python quickstart (reference)", "url": "https://developers.google.com/youtube/v3/quickstart/python", "what": "Google's official walkthrough of the same steps.", "ready": ""},
            {"name": "Audit & quota extension form", "url": "https://support.google.com/youtube/contact/yt_api_form", "what": "Needed to upload Public directly and to raise the daily upload limit.", "ready": "Project number, description of the app."},
        ],
    },
]
